#!/bin/bash
# Test MPS sharing by running multiple pods simultaneously

echo "Testing MPS GPU sharing - launching 3 pods simultaneously"
echo "=============================================="
echo ""

# Launch 3 pods at the same time
for i in 1 2 3; do
  cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: nvidia-mps-concurrent-test-$i
  namespace: nvidia-device-plugin
spec:
  runtimeClassName: nvidia
  restartPolicy: Never
  nodeSelector:
    nvidia.com/gpu.present: "true"
  containers:
  - name: cuda-test
    image: nvcr.io/nvidia/cuda:12.4.1-base-ubuntu22.04
    command:
      - /bin/bash
      - -c
      - |
        echo "Pod $i starting at \$(date)"
        nvidia-smi
        echo "Sleeping 30 seconds..."
        sleep 30
        echo "Pod $i completed at \$(date)"
    volumeMounts:
      - name: mps-root
        mountPath: /mps
        readOnly: false
    resources:
      limits:
        nvidia.com/gpu: 1
      requests:
        nvidia.com/gpu: 1
  volumes:
    - name: mps-root
      hostPath:
        path: /run/nvidia/mps
        type: Directory
EOF
  echo "Launched pod $i"
done

echo ""
echo "Waiting for pods to start..."
sleep 5

echo ""
echo "Pod status:"
kubectl get pods -n nvidia-device-plugin | grep nvidia-mps-concurrent-test

echo ""
echo "If MPS is working, all 3 pods should be Running simultaneously"
echo "Without MPS, only 1 would run at a time (others Pending)"
echo ""
echo "Monitor with: watch kubectl get pods -n nvidia-device-plugin"
echo "View logs: kubectl logs nvidia-mps-concurrent-test-1 -n nvidia-device-plugin"
