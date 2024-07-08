import kopf
import kubernetes.client
from kubernetes.client.rest import ApiException
from kubernetes import config
import logging
from functools import reduce
  
logger = logging.getLogger(__name__)

config.load_incluster_config()

VPA_CONFIGS = {}

def get_all_configs():
    configs = {}
    api = kubernetes.client.CustomObjectsApi()
    try:
        crds = api.list_cluster_custom_object(
            group="autoscaling.k8s.io",
            version="v1",
            plural="autovpaconfigs"
        )
        configs = {}
        for item in crds["items"]:
            namespace = item["metadata"]["namespace"]
            excluded_deployments = deep_get(item, ["spec","excludedDeployments"], [])
            resource_policy = {
              "containerPolicies": [ deep_get(item, ["spec","resourcePolicy"], {}) ]
            },
            update_policy = deep_get(item, ["spec","updatePolicy"], {"update_mode":"Off"})
            configs[namespace] = {
                "excluded_deployments": excluded_deployments,
                "resource_policy": resource_policy,
                "update_policy": update_policy
            }
    except ApiException as e:
        if e.status == 404:
            logger.info("No VPAConfig CRs found")
        else:
            logger.error(f"Failed to fetch VPAConfig CRs: {e}")
    return configs

def update_vpa_configs():
    global VPA_CONFIGS
    VPA_CONFIGS = get_all_configs()

def filter_resources(namespace, annotations, name, **_):
    return namespace in VPA_CONFIGS and name not in VPA_CONFIGS[namespace]["excluded_deployments"] and str2bool(annotations.get("autovpa.autoscaling.k8s.io/enabled", "true"))

def filter_resources_only_namespace(namespace, **_):
    return namespace in VPA_CONFIGS

def create_vpa_for_deployment(name, namespace):
    api_instance = kubernetes.client.CustomObjectsApi()
    config = VPA_CONFIGS[namespace]
    vpa_body = {
        "apiVersion": "autoscaling.k8s.io/v1",
        "kind": "VerticalPodAutoscaler",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "annotations": {
                "autovpa.autoscaling.k8s.io/deployment": name
            }
        },
        "spec": {
            "targetRef": {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "name": name
            },
            "updatePolicy": config["update_policy"],
            "resourcePolicy": config["resource_policy"]
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
        logger.info(f"VPA created for deployment {name} in namespace {namespace}")
    except ApiException as e:
        if e.status != 409:  # Ignore conflict errors if the VPA already exists
            logger.error(f"Failed to create VPA for deployment {name} in namespace {namespace}: {e}")

def delete_vpa_for_deployment(name, namespace):
    api_instance = kubernetes.client.CustomObjectsApi()
    #check the vpa annotation

    try:
        api_response = api_instance.get_namespaced_custom_object(
            group="autoscaling.k8s.io",
            version="v1",
            namespace=namespace,
            plural="verticalpodautoscalers",
            name=name,
        )

        if deep_get(api_response, ["metadata","annotations","autovpa.autoscaling.k8s.io/deployment"], "") == name:
            api_instance.delete_namespaced_custom_object(
                group="autoscaling.k8s.io",
                version="v1",
                namespace=namespace,
                plural="verticalpodautoscalers",
                name=name,

            )
            logger.info(f"VPA deleted for deployment {name} in namespace {namespace}")
    except ApiException as e:
        if e.status != 404:
            logger.error(f"Failed to delete VPA for deployment {name} in namespace {namespace}: {e}")

def update_vpa(namespace, new_config):
    api_instance = kubernetes.client.CustomObjectsApi()
    try:
        vpas = api_instance.list_namespaced_custom_object(
            group="autoscaling.k8s.io",
            version="v1",
            namespace=namespace,
            plural="verticalpodautoscalers"
        )
        for vpa in vpas["items"]:
            vpa_name = vpa["metadata"]["name"]
            vpa["spec"]["updatePolicy"] = new_config["update_policy"]
            vpa["spec"]["resourcePolicy" = new_config["resource_policy"]
            api_instance.patch_namespaced_custom_object(
                group="autoscaling.k8s.io",
                version="v1",
                namespace=namespace,
                plural="verticalpodautoscalers",
                name=vpa_name,
                body=vpa
            )
            logger.info(f"VPA {vpa_name} in namespace {namespace} updated with new configuration")
    except ApiException as e:
        logger.error(f"Failed to update VPAs in namespace {namespace} with new configuration: {e}")

@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, **_):
    settings.posting.enabled = False
    #settings.persistence.finalizer = 'autovpa.autoscaling.k8s.io/finalizer'
    update_vpa_configs()

@kopf.on.create('deployments', when=filter_resources)
def create_vpa(body, meta, spec, name, namespace, **_):
    create_vpa_for_deployment(name, namespace)

@kopf.on.delete('deployments', when=filter_resources)
def delete_vpa(body, meta, spec, name, namespace, **_):
    delete_vpa_for_deployment(name, namespace)

@kopf.on.update('deployments', when=filter_resources_only_namespace)
def update_deployment(body, meta, spec, name, namespace, annotations, **_):
    vpa_enabled = str2bool(annotations.get("autovpa.autoscaling.k8s.io/enabled", "true"))
    excluded_deployments = VPA_CONFIGS[namespace]["excluded_deployments"]
    if namespace in VPA_CONFIGS and name not in excluded_deployments and vpa_enabled:
        create_vpa_for_deployment(name, namespace)
    else:
        delete_vpa_for_deployment(name, namespace)

@kopf.on.create('autovpaconfigs', group='autoscaling.k8s.io')
@kopf.on.update('autovpaconfigs', group='autoscaling.k8s.io')
def handle_vpaconfig_change(spec, name, namespace, **_):
    old_config = VPA_CONFIGS.get(namespace, {})
    update_vpa_configs()
    new_config = VPA_CONFIGS[namespace]
    if old_config != new_config:
        update_vpa(namespace, new_config)
    if namespace in VPA_CONFIGS:
        excluded_deployments = VPA_CONFIGS[namespace]["excluded_deployments"]
        api_instance = kubernetes.client.AppsV1Api()
        deployments = api_instance.list_namespaced_deployment(namespace=namespace)
        for deployment in deployments.items:
            dep_name = deployment.metadata.name
            dep_annotations = deployment.metadata.annotations or {}
            vpa_enabled = str2bool(dep_annotations.get("autovpa.autoscaling.k8s.io/enabled", "true"))
            if dep_name in excluded_deployments or not vpa_enabled:
                delete_vpa_for_deployment(dep_name, namespace)
            elif dep_name not in excluded_deployments and vpa_enabled:
                create_vpa_for_deployment(dep_name, namespace)

@kopf.on.delete('autovpaconfigs', group='autoscaling.k8s.io')
def handle_vpaconfig_delete(spec, name, namespace, **_):
    if namespace in VPA_CONFIGS:
        excluded_deployments = VPA_CONFIGS[namespace]["excluded_deployments"]
        api_instance = kubernetes.client.AppsV1Api()
        deployments = api_instance.list_namespaced_deployment(namespace=namespace)
        for deployment in deployments.items:
            dep_name = deployment.metadata.name
            dep_annotations = deployment.metadata.annotations or {}
            vpa_enabled = str2bool(dep_annotations.get("autovpa.autoscaling.k8s.io/enabled", "true"))
            if dep_name not in excluded_deployments and vpa_enabled:
                delete_vpa_for_deployment(dep_name, namespace)
    update_vpa_configs()

def deep_get(dictionary, keys, default=None):
    return reduce(lambda d, key: d.get(key, default) if isinstance(d, dict) else default, keys, dictionary)

def str2bool(v):
  return v.lower() in ("yes", "true", "t", "1")
