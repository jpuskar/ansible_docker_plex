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
ansible-playbook playbooks/naskiller.yml
```