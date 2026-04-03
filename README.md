
```bash
_ANSIBLE_VAULT_PASSWORD_PATH=/home/john/.ansible-vault-password;ARGOCD_SOPS_AGE_KEY_PATH=/home/john/.ansible_sops_age_key;LUKS_PASSWORD_PATH=/home/john/.luks_password_path
```

```bash
vagrant up
vagrant ssh
sudo su
```

```bash
yum update -y
yum install -y python-pip
pip install ansible
# add keys
#vi ~/.ssh/id_rsa
#vi ~/.ssh/id_rsa.pub
```

```bash
ansible-galaxy collection install community.general
```

```bash
ansible-playbook playbooks/naskiller.yml
```