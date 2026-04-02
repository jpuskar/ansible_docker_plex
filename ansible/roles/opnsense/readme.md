# OPNsense Ansible Role

# Ref:
1. https://ansible-opnsense.oxl.app/
2. https://github.com/O-X-L/ansible-opnsense


# TODO:
A lot of this is hallucinated and needs moved to oxlorg.opnsense.


# Initial Setup
1. Install OPNSenese
2. Configure LAN and WAN interfaces
3. Generate admin creds and API key


# TODO:
1. Test connection and creds
2. Assert that LAN and WAN are already correct
3. Assert that expected physical interfaces exist
4. Misc Configs
   1. hardware offload
   2. domain-name
   3. hostname
   4. timezone
   5. dns servers
5. Plugins
   1. acme
   2. ddclient
   3. dmidecode
   4. mdns repeater
   5. upnp
   6. microcode intel
6. Gateways config
7. Create VLANs and assignments
8. Wireguard and rules
9. Firewall rules
10. DHCP and DNS
11. NTP
12. DDNS
13. mDNS
14. uPnP
