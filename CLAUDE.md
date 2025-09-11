# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is an Ansible automation project for managing a Docker-based Plex media server infrastructure with ZFS storage and NFS file sharing. The project manages multiple server types including Plex servers, NFS storage servers, and UniFi Docker containers.

## Common Commands

### Running Playbooks
```bash
# Provision all servers (Plex and NFS storage)
ansible-playbook playbooks/provision_servers.yml

# Deploy UniFi Docker container
ansible-playbook playbooks/unifi_docker.yml

# Run specific playbook with custom inventory
ansible-playbook -i inventory/inventory.ini playbooks/provision_servers.yml
```

### Development Environment
```bash
# Set up local Vagrant development environment
vagrant up
vagrant ssh
sudo su

# Install Ansible in Vagrant (CentOS/Fedora)
yum update -y
yum install -y python-pip
pip install ansible
```

### Ansible Configuration
- Inventory file: `./inventory/inventory.ini`
- Roles path: `./roles`
- Vault password file: `./vault_pass`
- Host key checking is disabled

## Architecture

### Server Groups
- **plex_master**: Plex media servers (plex1, plex3) - runs Docker containers with Plex
- **nfs_master**: NFS storage servers (backups1) - provides ZFS storage and NFS exports
- **unifi_docker**: UniFi controller servers

### Key Roles
- **common**: Base system configuration, package updates, essential tools
- **docker**: Docker CE installation and configuration
- **plex**: Plex Docker container deployment, user management, firewall, NFS mounts
- **plex_nfs_server**: NFS server setup, ZFS management, exports, bind mounts, udev rules
- **zfs**: ZFS repository and package installation
- **tw_cli**: 3ware RAID controller management
- **UBUNTU22-CIS**: CIS hardening for Ubuntu 22.04 (security compliance)

### Storage Architecture
- Uses ZFS for storage pools (tank0, etc.)
- LUKS encryption for disk security
- NFS exports for sharing media between servers
- 3ware hardware RAID controllers
- Automated disk replacement procedures documented in `docs/zfs-disk-replacement.md`

### Security Features
- LUKS disk encryption with key files in `/etc/.keys/`
- Udev rules for automatic disk mapping and encryption
- CIS security hardening role for Ubuntu systems
- Firewall configuration through firewalld

## Development Notes

### File Structure
- `playbooks/`: Main automation playbooks
- `roles/`: Ansible roles for different server components
- `inventory/`: Server inventory definitions
- `docs/`: Documentation including ZFS disk replacement procedures
- `ansible.cfg`: Ansible configuration with inventory and roles paths

### Storage Management
ZFS disk replacement workflow is documented in `docs/zfs-disk-replacement.md` and includes:
- Disk partitioning with parted
- LUKS encryption setup
- ZFS pool attachment/replacement
- Udev rules for automatic mounting

### Docker Services
The infrastructure runs Plex in Docker containers with:
- Persistent data in `/data/docker/plex_nfs/config`
- NFS mounts for media access
- Custom docker_plex user/group
- Firewall rules for Plex ports