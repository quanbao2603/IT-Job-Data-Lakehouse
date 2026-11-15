#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${HOME}/IT-Job-Data-Lakehouse"
AIRFLOW_HOME="${HOME}/airflow"
AIRFLOW_VENV="${HOME}/airflow_env"
AIRFLOW_VERSION="2.10.4"
PYTHON_BIN="$(command -v python3)"

if [[ ! -d "${PROJECT_ROOT}" ]]; then
  echo "Project not found: ${PROJECT_ROOT}" >&2
  exit 1
fi

sudo apt-get update
sudo apt-get install -y postgresql postgresql-contrib build-essential libpq-dev

if [[ ! -d "${AIRFLOW_VENV}" ]]; then
  "${PYTHON_BIN}" -m venv "${AIRFLOW_VENV}"
fi
"${AIRFLOW_VENV}/bin/pip" install --upgrade pip setuptools wheel
"${AIRFLOW_VENV}/bin/pip" install "apache-airflow==${AIRFLOW_VERSION}" --constraint \
  "https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-3.10.txt"
"${AIRFLOW_VENV}/bin/pip" install -r "${PROJECT_ROOT}/infrastructure/airflow/requirements.txt"

sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='airflow'" | grep -q 1 || \
  sudo -u postgres psql -c "CREATE USER airflow WITH PASSWORD 'CHANGE_THIS_AIRFLOW_DB_PASSWORD';"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='airflow'" | grep -q 1 || \
  sudo -u postgres createdb -O airflow airflow

mkdir -p "${AIRFLOW_HOME}/dags" "${AIRFLOW_HOME}/logs" "${AIRFLOW_HOME}/plugins"
cat > "${AIRFLOW_HOME}/airflow.env" <<EOF
AIRFLOW_HOME=${AIRFLOW_HOME}
AIRFLOW__CORE__EXECUTOR=LocalExecutor
AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=postgresql+psycopg2://airflow:CHANGE_THIS_AIRFLOW_DB_PASSWORD@127.0.0.1:5432/airflow
AIRFLOW__CORE__LOAD_EXAMPLES=False
AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION=True
AIRFLOW__WEBSERVER__EXPOSE_CONFIG=False
LAKEHOUSE_PROJECT_ROOT=${PROJECT_ROOT}
LAKEHOUSE_PYTHON=${PROJECT_ROOT}/lakehouse_env/bin/python
LAKEHOUSE_INCREMENTAL_MAX_PAGES=5
EOF
chmod 600 "${AIRFLOW_HOME}/airflow.env"

set -a
source "${AIRFLOW_HOME}/airflow.env"
set +a
"${AIRFLOW_VENV}/bin/airflow" db migrate

if ! "${AIRFLOW_VENV}/bin/airflow" users list | grep -q 'quanbao'; then
  "${AIRFLOW_VENV}/bin/airflow" users create \
    --username quanbao \
    --firstname Quan \
    --lastname Bao \
    --role Admin \
    --email CHANGE_THIS_ADMIN_EMAIL \
    --password CHANGE_THIS_AIRFLOW_ADMIN_PASSWORD
fi

ln -sfn "${PROJECT_ROOT}/dags" "${AIRFLOW_HOME}/dags/lakehouse"

echo "Airflow installed. Edit ${AIRFLOW_HOME}/airflow.env, then create systemd services and start them."
