# Gateway API Role

Installs the Kubernetes Gateway API CRDs, which are required before enabling Gateway API support in Cilium.

## Background

Cilium does not automatically install the Gateway API CRDs via its Helm chart. They must be pre-installed as a prerequisite.

References:
- [Cilium Gateway API Documentation](https://docs.cilium.io/en/stable/network/servicemesh/gateway-api/gateway-api/)
- [Feature Request: Install Gateway API CRDs via Helm](https://github.com/cilium/cilium/issues/39843)
- [Gateway API Official Documentation](https://gateway-api.sigs.k8s.io/guides/)

## Variables

- `gateway_api_version`: Version of Gateway API to install (default: `v1.2.0` - matches Cilium 1.18.5 requirement)
- `gateway_api_channel`: Which channel to install - `standard` or `experimental` (default: `standard`)
  - `standard`: Includes core CRDs (GatewayClass, Gateway, HTTPRoute, etc.)
  - `experimental`: Includes additional experimental CRDs like TLSRoute

## Usage

This role is automatically included in `playbooks/bootstrap-k8s2.yml` before the Cilium role.

To install manually or upgrade:

```bash
ansible-playbook playbooks/bootstrap-k8s2.yml --tags gateway-api
```

## Installed CRDs

Standard channel includes:
- `gatewayclasses.gateway.networking.k8s.io`
- `gateways.gateway.networking.k8s.io`
- `grpcroutes.gateway.networking.k8s.io`
- `httproutes.gateway.networking.k8s.io`
- `referencegrants.gateway.networking.k8s.io`

## Version Compatibility

- Cilium 1.18.5 requires Gateway API v1.2.0
- Cilium 1.19.0 requires Gateway API v1.3.0
