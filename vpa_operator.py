import kopf
import kubernetes.client
from kubernetes.client.rest import ApiException

NAMESPACE_TO_WATCH = 'default'  # Change this to limit to a specific namespace
EXCLUDED_DEPLOYMENTS = ['exclude-this-deployment']  # Add deployments to exclude

@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, **_):
    settings.posting.level = 'INFO'

@kopf.on.create('apps', 'v1', 'deployments', when=lambda body, **_: body.metadata.namespace == NAMESPACE_TO_WATCH and body.metadata.name not in EXCLUDED_DEPLOYMENTS)
def create_vpa(spec, name, namespace, **_):
    api_instance = kubernetes.client.CustomObjectsApi()
    vpa_body = {
        "apiVersion": "autoscaling.k8s.io/v1",
        "kind": "VerticalPodAutoscaler",
        "metadata": {
            "name": name,
            "namespace": namespace
        },
        "spec": {
            "targetRef": {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "name": name
            },
            "updatePolicy": {
                "updateMode": "Auto"
            }
        }
    }

    try:
        api_instance.create_namespaced_custom_object(
            group="autoscaling.k8s.io",
            version="v1",
            namespace=namespace,
            plural="verticalpodautoscalers",
            body=vpa_body
        )
        kopf.info(body=spec, reason="Created", message=f"VPA created for deployment {name}")
    except ApiException as e:
        kopf.exception(body=spec, reason="CreateFailed", message=f"Failed to create VPA for deployment {name}: {e}")

@kopf.on.delete('apps', 'v1', 'deployments', when=lambda body, **_: body.metadata.namespace == NAMESPACE_TO_WATCH and body.metadata.name not in EXCLUDED_DEPLOYMENTS)
def delete_vpa(spec, name, namespace, **_):
    api_instance = kubernetes.client.CustomObjectsApi()
    try:
        api_instance.delete_namespaced_custom_object(
            group="autoscaling.k8s.io",
            version="v1",
            namespace=namespace,
            plural="verticalpodautoscalers",
            name=name
        )
        kopf.info(body=spec, reason="Deleted", message=f"VPA deleted for deployment {name}")
    except ApiException as e:
        kopf.exception(body=spec, reason="DeleteFailed", message=f"Failed to delete VPA for deployment {name}: {e}")
