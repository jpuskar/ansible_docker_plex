import argparse
import base64
import configparser
import os

import kubernetes
import yaml

parser = argparse.ArgumentParser()
parser.add_argument('--configmap-file-path', type=str, default='/config/config.yaml')
parser.add_argument('--palworld-config-file-path', type=str, default='/server/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini')
parser.add_argument('--palworld-config-template-file-path', type=str, default='/server/DefaultPalWorldSettings.ini')
parser.add_argument('--secret-name', type=str, default='server-password')
parser.add_argument('--secret-namespace', type=str)  # TODO: Default to my own namespace
parser.add_argument('--secret-server-password-key', type=str, default='server-password')
parser.add_argument('--secret-admin-password-key', type=str, default='admin-password')


header = '[/Script/Pal.PalGameWorldSettings]'


def configure():

    with open(CONFIGMAP_FILE_PATH, 'r') as file:
        configmap_data = yaml.safe_load(file)
    from pprint import pprint
    pprint(configmap_data)

    palworld_config = configparser.ConfigParser()
    if os.path.isfile(PALWORLD_CONFIG_FILE_PATH):
        palworld_config.read(PALWORLD_CONFIG_FILE_PATH)
    else:
        palworld_config.read(PALWORLD_CONFIG_TEMPLATE_FILE_PATH)
    option_settings_str = palworld_config['/Script/Pal.PalGameWorldSettings']['OptionSettings']
    option_settings_str = str(option_settings_str).lstrip('(').rstrip(')')
    # option_settings_str = option_settings_str.replace(',', '\n')
    # pprint(option_settings_str)

    options_dict = {}
    # TODO: use csv parser just in case
    for line in option_settings_str.split(','):
        # TODO: assert '=' in line
        if line:
            key, value = line.split('=', 1)
            options_dict[key] = value

    for key in configmap_data.keys():
        # TODO: make sure my override value is a string
        options_dict[key] = configmap_data[key]

    api_instance = kubernetes.client.CoreV1Api()
    secret = api_instance.read_namespaced_secret(SECRET_NAME, SECRET_NAMESPACE)

    server_password_encoded = secret.data.get(SECRET_SERVER_PASSWORD_KEY)
    server_password = base64.b64decode(server_password_encoded).decode("utf-8")
    options_dict['ServerPassword'] = server_password

    admin_password_encoded = secret.data.get(SECRET_ADMIN_PASSWORD_KEY)
    admin_password = base64.b64decode(admin_password_encoded).decode("utf-8")
    options_dict['AdminPassword'] = admin_password

    pprint(options_dict)

    options_dict_final_str_parts = []
    for key in options_dict.keys():
        val = options_dict[key]
        # TODO: add quotes if string and not float/int/bool/none
        if 'password' in str(key).lower():
            val = '"' + val + '"'
        else:
            pass
        options_dict_final_str_parts.append(f'{key}={val}')

    final_config = ''.join(
        [
            header,
            '\n',
            'OptionSettings=',
            '(',
            ','.join(options_dict_final_str_parts),
            ')',
        ]
    )
    pprint('************************************')
    print(final_config)
    pprint('************************************')

    with open(PALWORLD_CONFIG_FILE_PATH, 'w') as configfile:
        configfile.write(final_config)


if __name__ == "__main__":
    # Parse the arguments
    args = parser.parse_args()
    CONFIGMAP_FILE_PATH = args.configmap_file_path
    PALWORLD_CONFIG_FILE_PATH = args.palworld_config_file_path
    PALWORLD_CONFIG_TEMPLATE_FILE_PATH = args.palworld_config_template_file_path
    SECRET_NAME = args.secret_name
    SECRET_NAMESPACE = args.secret_namespace
    SECRET_SERVER_PASSWORD_KEY = args.secret_server_password_key
    SECRET_ADMIN_PASSWORD_KEY = args.secret_admin_password_key

    kubernetes.config.load_incluster_config()
    configure()
