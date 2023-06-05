#!/bin/bash
set -e

cp sql/framework.sql sql/framework1.sql
OLD_SQL_USER_TAG="ccio"
NEW_SQL_USER_TAG="$DB_DATABASE"
sed -i "s/$OLD_SQL_USER_TAG/$NEW_SQL_USER_TAG/g" sql/framework1.sql
if [[ "${SSL_ENABLED}" == "true" ]]; then
  if [[ -d /config/ssl ]]; then
    echo "Using provided SSL Key"
    cp -R /config/ssl ssl
    SSL_CONFIG='{"key":"./ssl/server.key","cert":"./ssl/server.cert"}'
  else
    echo "Making new SSL Key"
    mkdir -p ssl
    SUBJ_SLUG=""
    SUBJ_SLUG="${SUBJ_SLUG}/C=$SSL_COUNTRY"
    SUBJ_SLUG="${SUBJ_SLUG}/ST=$SSL_STATE"
    SUBJ_SLUG="${SUBJ_SLUG}/L=$SSL_LOCATION"
    SUBJ_SLUG="${SUBJ_SLUG}/O=$SSL_ORGANIZATION"
    SUBJ_SLUG="${SUBJ_SLUG}/OU=$SSL_ORGANIZATION_UNIT"
    SUBJ_SLUG="${SUBJ_SLUG}/CN=$SSL_COMMON_NAME"
    openssl req \
      -nodes \
      -new \
      -x509 \
      -keyout ssl/server.key \
      -out ssl/server.cert \
      -subj "${SUBJ_SLUG}"
    cp -R ssl /config/ssl
    SSL_CONFIG=$(
      jq -n \
        --arg cert_key_file "./ssl/server.key" \
        --arg cert_file "./ssl/server.cert" \
        '{key: ($cert_key_file), cert: ($cert_file)}'
    )
  fi
else
  SSL_CONFIG='{}'
fi

if [[ "${DB_DISABLE_INCLUDED}" == "false" ]]; then
  if [[ ! -f "/var/lib/mysql/ibdata1" ]]; then
    echo "Installing MariaDB ..."
    mysql_install_db --user=mysql --datadir=/var/lib/mysql --silent
  fi
  echo "Starting MariaDB ..."
  /usr/bin/mysqld_safe --user=mysql &
  sleep 5s

  chown -R mysql /var/lib/mysql

  if [[ ! -f "/var/lib/mysql/ibdata1" ]]; then
    mysql -u root --password="" -e "SET @@SESSION.SQL_LOG_BIN=0;
        USE mysql;
        DELETE FROM mysql.user ;
        DROP USER IF EXISTS 'root'@'%','root'@'localhost','${DB_USER}'@'localhost','${DB_USER}'@'%';
        CREATE USER 'root'@'%' IDENTIFIED BY '${DB_PASS}' ;
        CREATE USER 'root'@'localhost' IDENTIFIED BY '${DB_PASS}' ;
        CREATE USER '${DB_USER}'@'%' IDENTIFIED BY '${DB_PASS}' ;
        CREATE USER '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASS}' ;
        GRANT ALL PRIVILEGES ON *.* TO 'root'@'%' WITH GRANT OPTION ;
        GRANT ALL PRIVILEGES ON *.* TO 'root'@'localhost' WITH GRANT OPTION ;
        GRANT ALL PRIVILEGES ON *.* TO '${DB_USER}'@'%' WITH GRANT OPTION ;
        GRANT ALL PRIVILEGES ON *.* TO '${DB_USER}'@'localhost' WITH GRANT OPTION ;
        DROP DATABASE IF EXISTS test ;
        FLUSH PRIVILEGES ;"
  fi

  # Create MySQL database if it does not exists
  if [[ -n "${DB_HOST}" ]]; then
    echo "Wait for MySQL server."
    while ! mysqladmin ping -h "${DB_HOST}"; do
      sleep 1
    done
  fi

  echo "Setting up MySQL database if it does not exist."

  echo "Create database schema if it does not exist."
  mysql -e "source /home/Shinobi/sql/framework.sql" || true

  echo "Create database user if it does not exist."
  mysql -e "source /home/Shinobi/sql/user.sql" || true

else
  echo "Create database schema if it does not exist."
  mysql \
    -u "${DB_USER}" \
    -h "${DB_HOST}" \
    -p "${DB_PASSWORD}" \
    --port="${DB_PORT}" \
    -e "source /home/Shinobi/sql/framework.sql" ||
    true
fi

DATABASE_CONFIG=$(
  jq -n \
    --arg db_host "${DB_HOST}" \
    --arg db_user "${DB_USER}" \
    --arg db_password "${DB_PASSWORD}" \
    --arg db_database "${DB_DATABASE}" \
    --arg db_port "${DB_PORT}" \
    '{host: ($db_host), user: ($db_user), password: ($db_password), database: ($db_database), port: ($db_port)}'
)

CRON_KEY="$(head -c 1024 </dev/urandom | sha256sum | awk '{print substr($1,1,29)}')"

cd /home/Shinobi
mkdir -p libs/customAutoLoad
if [[ -e "/config/conf.json" ]]; then
  cp /config/conf.json conf.json
fi
if [[ ! -e "./conf.json" ]]; then
  sudo cp conf.sample.json conf.json
fi
sudo sed -i -e 's/change_this_to_something_very_random__just_anything_other_than_this/'"${CRON_KEY}"'/g' conf.json
node tools/modifyConfiguration.js \
  cpuUsageMarker=CPU \
  subscriptionId="${SUBSCRIPTION_ID}" \
  thisIsDocker=true \
  pluginKeys="${PLUGIN_KEYS}" \
  db="${DATABASE_CONFIG}" \
  ssl="${SSL_CONFIG}"
sudo cp conf.json /config/conf.json

echo "============="
echo "Default Superuser : admin@shinobi.video"
echo "Default Password : admin"
echo "Log in at http://HOST_IP:SHINOBI_PORT/super"
if [[ -e "/config/super.json" ]]; then
  cp /config/super.json super.json
fi
if [[ ! -e "./super.json" ]]; then
  sudo cp super.sample.json super.json
  sudo cp super.sample.json /config/super.json
fi

# Execute Command
echo "Starting Shinobi."
exec "$@"
