import argparse
import base64
import configparser
import os
import subprocess

import kubernetes
import yaml


parser = argparse.ArgumentParser()
parser.add_argument('--secret-name', type=str, default='server-password')
parser.add_argument('--secret-namespace', type=str)  # TODO: Default to my own namespace
parser.add_argument('--secret-server-password-key', type=str, default='server-password')
parser.add_argument('--secret-admin-password-key', type=str, default='admin-password')
parser.add_argument('--rcon-port', type=int, default=25575)


def run_backup():
    api_instance = kubernetes.client.CoreV1Api()
    secret = api_instance.read_namespaced_secret(SECRET_NAME, SECRET_NAMESPACE)

    admin_password_encoded = secret.data.get(SECRET_ADMIN_PASSWORD_KEY)
    admin_password = base64.b64decode(admin_password_encoded).decode("utf-8")

    output = subprocess.check_call(
        [
            '/usr/bin/rcon-cli',
            '--host',
            'localhost',
            '--port',
            str(RCON_PORT),
            '--password',
            admin_password,
            'save',
        ],
        stderr=subprocess.STDOUT,
        text=True,
    )
    print("Command output:")
    print(output)


if __name__ == "__main__":
    # Parse the arguments
    args = parser.parse_args()
    SECRET_NAME = args.secret_name
    SECRET_NAMESPACE = args.secret_namespace
    SECRET_SERVER_PASSWORD_KEY = args.secret_server_password_key
    SECRET_ADMIN_PASSWORD_KEY = args.secret_admin_password_key
    RCON_PORT = args.rcon_port

    kubernetes.config.load_incluster_config()
    run_backup()
