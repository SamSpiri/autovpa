### Automatically creates Vertical Pod Autoscalers (VPA) for all deployments in a namespace

Example custom resource:
```yaml
apiVersion: autoscaling.k8s.io/v1
kind: AutoVPA
metadata:
  # Hardcoded name. Only one config per namespace is allowed
  name: autoautovpa-operator-config
  namespace: default
spec:
  updatePolicy:
    # "Off", "Initial", "Recreate", and "Auto".
    updateMode: Off
    minReplicas: 1
  resourcePolicy:
    mode: Auto
    minAllowed:
      cpu: 15m
      memory: 100Mi
    maxAllowed:
      cpu: 1
      memory: 20Gi
    controlledResources:
      - cpu
      - memory
    # "RequestsOnly", "RequestsAndLimits"
    controlledValues: "RequestsOnly"
  excludedDeployments:
    - "exclude-this-deployment"
```

Example annotation for deployment to exclude it from AutoVPA. If you don't have this annotation set, or set it to true, you will get a VPA for the deployment.
```yaml
annotations:
  autovpa.autoscaling.k8s.io/enabled: "false"
```