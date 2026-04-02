#!/bin/bash
set -e

# Setup SSH host keys in user's home directory
if [ ! -f ~/.ssh/sshd/ssh_host_rsa_key ]; then
    echo "Generating SSH host keys..."
    ssh-keygen -t rsa -f ~/.ssh/sshd/ssh_host_rsa_key -N '' -q
    ssh-keygen -t ecdsa -f ~/.ssh/sshd/ssh_host_ecdsa_key -N '' -q
    ssh-keygen -t ed25519 -f ~/.ssh/sshd/ssh_host_ed25519_key -N '' -q
fi

# Setup authorized keys
if [ -f /tmp/ssh-keys/authorized_keys ]; then
    echo "Setting up SSH authorized keys..."
    cp /tmp/ssh-keys/authorized_keys ~/.ssh/authorized_keys
    chmod 600 ~/.ssh/authorized_keys
fi

# Create sshd_config for non-privileged mode
cat > ~/.ssh/sshd/sshd_config <<EOF
Port ${SSH_PORT}
PidFile ${HOME}/.ssh/sshd/sshd.pid
HostKey ${HOME}/.ssh/sshd/ssh_host_rsa_key
HostKey ${HOME}/.ssh/sshd/ssh_host_ecdsa_key
HostKey ${HOME}/.ssh/sshd/ssh_host_ed25519_key

# Authentication
PubkeyAuthentication yes
PasswordAuthentication no
PermitRootLogin no
PermitEmptyPasswords no
ChallengeResponseAuthentication no
KerberosAuthentication no
GSSAPIAuthentication no
AuthorizedKeysFile ${HOME}/.ssh/authorized_keys

# Non-privileged mode settings
UsePAM no
UsePrivilegeSeparation no
StrictModes yes

# Session limits
MaxAuthTries 3
MaxSessions 10
LoginGraceTime 30

# Performance
UseDNS no

# Connection keepalive
ClientAliveInterval 300
ClientAliveCountMax 2

# Logging
SyslogFacility AUTH
LogLevel INFO

# Security
X11Forwarding no
AllowTcpForwarding yes
AllowAgentForwarding yes
PermitTunnel no
PermitUserEnvironment no

# Modern crypto only
KexAlgorithms curve25519-sha256,curve25519-sha256@libssh.org,diffie-hellman-group-exchange-sha256
Ciphers chacha20-poly1305@openssh.com,aes256-gcm@openssh.com,aes128-gcm@openssh.com,aes256-ctr,aes192-ctr,aes128-ctr
MACs hmac-sha2-512-etm@openssh.com,hmac-sha2-256-etm@openssh.com,hmac-sha2-512,hmac-sha2-256
EOF

# Start SSH daemon as non-root user
echo "Starting SSH server on port ${SSH_PORT} as user ${USER}..."
/usr/sbin/sshd -D -e -f ~/.ssh/sshd/sshd_config
