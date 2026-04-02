# NVIDIA MPS GPU Testing

This directory contains test manifests for validating NVIDIA MPS (Multi-Process Service) GPU sharing.

## Configuration

Based on your current config, each GPU is configured to support **10 replicas** via MPS sharing.

## Test Files

### 1. `quick-test.yaml`
Quick test - runs `nvidia-smi` and exits immediately:

```bash
kubectl apply -f quick-test.yaml
kubectl logs nvidia-quick-test
kubectl delete pod nvidia-quick-test
```

### 2. `gpu-info.yaml`
Detailed GPU information and status:

```bash
kubectl apply -f gpu-info.yaml
kubectl logs nvidia-gpu-info
kubectl delete pod nvidia-gpu-info
```

### 3. `mps-sharing-test.yaml`
Test MPS GPU sharing by running multiple pods simultaneously:

```bash
# Terminal 1 - Apply first test pod
kubectl apply -f mps-sharing-test.yaml

# Terminal 2 - While first pod is running, apply second pod with different name
kubectl apply -f mps-sharing-test.yaml -o yaml | sed 's/nvidia-mps-test/nvidia-mps-test-2/' | kubectl apply -f -

# Terminal 3 - Apply third pod
kubectl apply -f mps-sharing-test.yaml -o yaml | sed 's/nvidia-mps-test/nvidia-mps-test-3/' | kubectl apply -f -

# Watch logs
kubectl logs -f nvidia-mps-test
```

If MPS is working correctly, all pods should run simultaneously on the same GPU (up to 10 based on replicas config).

### 4. `cuda-vectoradd-test.yaml`
CUDA computation test using NVIDIA's official sample:

```bash
kubectl apply -f cuda-vectoradd-test.yaml
kubectl logs -f nvidia-cuda-vectoradd
kubectl delete pod nvidia-cuda-vectoradd
```

## Verifying MPS is Working

1. **Check device plugin logs:**
   ```bash
   kubectl logs -n nvidia-device-plugin -l app.kubernetes.io/name=nvidia-device-plugin -c nvidia-device-plugin
   ```

2. **Check MPS server is running on the node:**
   ```bash
   # SSH to your GPU node (k8s7)
   ps aux | grep nvidia-cuda-mps
   ```

3. **Run multiple pods simultaneously:**
   - Without MPS: Only 1 pod can use the GPU at a time
   - With MPS: Up to 10 pods can share the GPU (based on your replicas: 10 config)

4. **Check GPU processes:**
   ```bash
   kubectl run nvidia-smi-pmon --rm -ti --restart=Never \
     --image=nvcr.io/nvidia/cuda:12.4.1-base-ubuntu22.04 \
     --overrides='{"spec":{"runtimeClassName":"nvidia","nodeSelector":{"nvidia.com/gpu.present":"true"},"containers":[{"name":"test","image":"nvcr.io/nvidia/cuda:12.4.1-base-ubuntu22.04","command":["nvidia-smi","pmon"],"resources":{"limits":{"nvidia.com/gpu":"1"}}}]}}'
   ```

## Troubleshooting

### Pod stuck in Pending
- Check if GPU node has `nvidia.com/gpu.present: "true"` label
- Verify device plugin is running: `kubectl get pods -n nvidia-device-plugin`

### "UnexpectedAdmissionError" or scheduling failures
- Check device plugin configuration
- Verify MPS config in helm values
- Look at device plugin logs for errors

### Pods not sharing GPU
- Verify config.map.default.sharing.mps is set correctly
- Check if MPS server pods are running
- Review device plugin logs for MPS initialization errors
