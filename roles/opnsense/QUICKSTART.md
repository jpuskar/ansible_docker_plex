# OPNsense Ansible Role - Quick Start Guide

This guide will help you get started with the OPNsense Ansible role in just a few minutes.

## Prerequisites

- OPNsense firewall (version 23.x or later)
- Ansible installed on your control machine
- Network connectivity to OPNsense
- OPNsense API credentials

## Step 1: Generate OPNsense API Credentials

1. Log into your OPNsense web interface
2. Navigate to: **System → Access → Users**
3. Select your user (or create a new one)
4. Scroll to the **API keys** section
5. Click the **+** button to generate a new API key
6. Copy both the **API Key** and **API Secret** (you'll need these later)

## Step 2: Store Credentials Securely

Create an Ansible vault file to store your credentials:

```bash
cd /home/john/git/ansible_docker_plex

# Create a vault file for OPNsense credentials
ansible-vault create group_vars/opnsense_firewalls/vault.yml
```

Add the following content (use the credentials from Step 1):

```yaml
---
vault_opnsense_api_key: "your-api-key-here"
vault_opnsense_api_secret: "your-api-secret-here"
```

Save and exit. You'll be prompted to create a vault password.

## Step 3: Create Inventory

Add your OPNsense firewall to your inventory:

```bash
# Edit or create inventory file
vim inventory/inventory.ini
```

Add this section:

```ini
[opnsense_firewalls]
firewall ansible_host=192.168.1.1

[opnsense_firewalls:vars]
ansible_connection=local
ansible_python_interpreter=/usr/bin/python3
```

## Step 4: Customize Configuration

Edit the defaults file to match your environment:

```bash
vim roles/opnsense/defaults/main.yml
```

Or create a custom vars file:

```bash
# Copy the example vars file
cp roles/opnsense/vars/example.yml group_vars/opnsense_firewalls/opnsense.yml

# Edit to match your environment
vim group_vars/opnsense_firewalls/opnsense.yml
```

Key variables to customize:

```yaml
opnsense_host: "192.168.1.1"  # Your firewall IP

# VLANs - adjust to match your network
opnsense_vlans:
  - device: igb0              # Change to your interface
    tag: 10
    description: "Management VLAN"

# DHCP ranges - adjust to match your subnets
opnsense_dhcp_servers:
  - interface: "lan"
    range_from: "192.168.1.100"
    range_to: "192.168.1.200"
    gateway: "192.168.1.1"
```

## Step 5: Test Connectivity

Before running the full playbook, test API connectivity:

```bash
ansible opnsense_firewalls -i inventory/inventory.ini \
  -m uri \
  -a "url=https://192.168.1.1/api/core/firmware/status \
      user={{ vault_opnsense_api_key }} \
      password={{ vault_opnsense_api_secret }} \
      force_basic_auth=yes \
      validate_certs=no" \
  --ask-vault-pass
```

If successful, you'll see JSON output with firmware status.

## Step 6: Run the Playbook

### Dry Run (Check Mode)

First, run in check mode to see what would change:

```bash
ansible-playbook playbooks/opnsense_config.yml \
  -i inventory/inventory.ini \
  --ask-vault-pass \
  --check
```

### Apply Configuration

When ready, apply the configuration:

```bash
ansible-playbook playbooks/opnsense_config.yml \
  -i inventory/inventory.ini \
  --ask-vault-pass
```

### Run Specific Sections

Configure only specific components using tags:

```bash
# Only configure VLANs
ansible-playbook playbooks/opnsense_config.yml \
  -i inventory/inventory.ini \
  --tags opnsense_vlans \
  --ask-vault-pass

# Only configure DHCP and DNS
ansible-playbook playbooks/opnsense_config.yml \
  -i inventory/inventory.ini \
  --tags opnsense_dhcp,opnsense_dns \
  --ask-vault-pass

# Skip firewall rules
ansible-playbook playbooks/opnsense_config.yml \
  -i inventory/inventory.ini \
  --skip-tags opnsense_rules \
  --ask-vault-pass
```

## Step 7: Verify Configuration

After running the playbook, verify the configuration in OPNsense:

1. **VLANs**: Interfaces → Other Types → VLAN
2. **Gateways**: System → Gateways → Single
3. **Routes**: System → Routes → Configuration
4. **DHCP**: Services → DHCPv4
5. **DNS**: Services → Unbound DNS → General
6. **Firewall Rules**: Firewall → Rules

## Common Use Cases

### Add a New VLAN

1. Edit your vars file:

```yaml
opnsense_vlans:
  - device: igb0
    tag: 50
    description: "New VLAN"
    priority: 0
```

2. Run the playbook with VLAN tag:

```bash
ansible-playbook playbooks/opnsense_config.yml \
  --tags opnsense_vlans \
  --ask-vault-pass
```

### Add DHCP Static Mapping

1. Add to your vars:

```yaml
opnsense_dhcp_static_mappings:
  - interface: "lan"
    mac: "11:22:33:44:55:66"
    ipaddr: "192.168.1.50"
    hostname: "newserver"
    descr: "New Server"
```

2. Run the playbook:

```bash
ansible-playbook playbooks/opnsense_config.yml \
  --tags opnsense_dhcp \
  --ask-vault-pass
```

### Add DNS Host Override

1. Add to your vars:

```yaml
opnsense_dns_hosts:
  - hostname: "newserver"
    domain: "home.local"
    ip: "192.168.1.50"
    descr: "New Server DNS"
```

2. Run the playbook:

```bash
ansible-playbook playbooks/opnsense_config.yml \
  --tags opnsense_dns \
  --ask-vault-pass
```

### Add Firewall Rule

1. Add to your vars:

```yaml
opnsense_firewall_rules:
  - interface: "lan"
    description: "Allow SSH from management"
    action: "pass"
    protocol: "tcp"
    source: "192.168.10.0/24"
    destination: "192.168.20.0/24"
    destination_port: "22"
```

2. Run the playbook:

```bash
ansible-playbook playbooks/opnsense_config.yml \
  --tags opnsense_rules \
  --ask-vault-pass
```

## Troubleshooting

### Issue: 401 Unauthorized

**Problem**: API credentials are incorrect or not being passed properly.

**Solution**:
1. Verify credentials in vault file
2. Check that vault password is correct
3. Ensure API key has proper permissions in OPNsense

### Issue: SSL Certificate Errors

**Problem**: Self-signed certificate validation failing.

**Solution**:
Set `opnsense_validate_certs: false` in your vars file.

### Issue: Changes Not Applied

**Problem**: Configuration added but not active in OPNsense.

**Solution**:
The role calls the API's apply/reconfigure endpoints. Check:
1. Playbook completed successfully
2. No errors in Ansible output
3. Check OPNsense logs: System → Log Files → Web GUI

### Issue: Duplicate Entries

**Problem**: Running the playbook multiple times creates duplicates.

**Solution**:
The OPNsense API `addItem` endpoints don't check for duplicates. Options:
1. Manually clean up in OPNsense GUI before re-running
2. Use tags to only run specific sections
3. Consider using the `puzzle.opnsense` collection for better idempotency

### Enable Debug Mode

Get detailed output:

```bash
ANSIBLE_DEBUG=1 ansible-playbook playbooks/opnsense_config.yml \
  -i inventory/inventory.ini \
  --ask-vault-pass \
  -vvv
```

## Next Steps

1. **Backup**: Always backup your OPNsense config before major changes
   - System → Configuration → Backups

2. **Version Control**: Commit your vars files to git (vault files are encrypted)

3. **CI/CD**: Integrate with your CI/CD pipeline for automated updates

4. **Monitoring**: Set up monitoring for configuration drift

5. **Documentation**: Update the example vars file as you add new configurations

## Additional Resources

- **Role README**: `roles/opnsense/readme.md`
- **API Reference**: `roles/opnsense/API_REFERENCE.md`
- **Example Variables**: `roles/opnsense/vars/example.yml`
- **OPNsense Docs**: https://docs.opnsense.org/

## Getting Help

If you run into issues:

1. Check the troubleshooting section above
2. Review OPNsense logs in the web interface
3. Test API calls manually using curl (see API_REFERENCE.md)
4. Enable debug output with `-vvv` flag

## Best Practices

1. **Start Small**: Begin with one component (e.g., DNS) and expand
2. **Test First**: Always use check mode before applying changes
3. **Backup**: Create OPNsense backups before automation runs
4. **Version Control**: Track all configuration changes in git
5. **Vault Everything**: Never commit plain-text credentials
6. **Tag Wisely**: Use tags to apply only relevant changes
7. **Verify**: Always verify in OPNsense GUI after changes

Happy automating!
