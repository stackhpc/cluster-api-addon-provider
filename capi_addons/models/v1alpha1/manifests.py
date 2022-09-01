import base64
import typing as t

from pydantic import Field, constr

from easykube import resources as k8s
from easykube.kubernetes.client import AsyncClient

from kube_custom_resource import schema

from ...template import Loader
from .base import EphemeralChartAddon, AddonSpec


# Type variable for forward references to the HelmRelease type
ManifestsType = t.TypeVar("ManifestsType", bound = "Manifests")


class ManifestSourceNameKeys(schema.BaseModel):
    """
    Model for a manifest source that consists of a resource name and sets of keys to
    explicitly include or exclude.
    """
    name: constr(regex = r"^[a-z0-9-]+$") = Field(
        ...,
        description = "The name of the resource to use."
    )
    keys: t.List[constr(min_length = 1)] = Field(
        default_factory = list,
        description = (
            "The keys in the resource to render as manifests. "
            "If not given, all the keys are considered."
        )
    )
    exclude_keys: t.List[constr(min_length = 1)] = Field(
        default_factory = list,
        description = "Keys to explicitly exclude from being rendered as manifests."
    )

    def filter_keys(self, keys: t.List[str]) -> t.List[str]:
        """
        Given a list of keys, return the keys that match the configured filters.
        """
        if self.keys:
            keys = (key for key in keys if key in self.keys)
        return [key for key in keys if key not in self.exclude_keys]


class ManifestConfigMapSource(schema.BaseModel):
    """
    Model for a manifest source that renders the keys in a configmap as Jinja2 templates.
    
    The templates are provided with the HelmRelease object, the Cluster API Cluster resource
    and the infrastructure cluster resource as template variables.
    """
    config_map: ManifestSourceNameKeys = Field(
        ...,
        description = "The details of a configmap and keys to use."
    )


class ManifestSecretSource(schema.BaseModel):
    """
    Model for a manifest source that renders the keys in a secret as Jinja2 templates.
    
    The templates are provided with the HelmRelease object, the Cluster API Cluster resource
    and the infrastructure cluster resource as template variables.
    """
    secret: ManifestSourceNameKeys = Field(
        ...,
        description = "The details of a secret and keys to use."
    )

    async def get_resources(
        self,
        template_loader: Loader,
        ek_client: AsyncClient,
        addon: ManifestsType,
        cluster: t.Dict[str, t.Any],
        infra_cluster: t.Dict[str, t.Any]
    ) -> t.Iterable[t.Dict[str, t.Any]]:
        secret = await k8s.Secret(ek_client).fetch(
            self.secret.name,
            namespace = addon.metadata.namespace
        )
        keys = self.secret.filter_keys(secret.data.keys())
        return (
            resource
            for key in keys
            for resource in template_loader.yaml_string_all(
                base64.b64decode(secret.data[key]).decode(),
                addon = addon,
                cluster = cluster,
                infra_cluster = infra_cluster
            )
        )


class ManifestTemplateSource(schema.BaseModel):
    """
    Model for a manifest source that renders the given string as as a Jinja2 template.
    
    The template is provided with the Manifests object, the Cluster API Cluster
    resource and the infrastructure cluster resource as template variables.
    """
    template: constr(min_length = 1) = Field(
        ...,
        description = "The template to use to render the manifests."
    )


ManifestSource = schema.StructuralUnion[
    ManifestConfigMapSource,
    ManifestSecretSource,
    ManifestTemplateSource,
]
ManifestSource.__doc__ = "Union type for the possible manifest sources."


class ManifestsSpec(AddonSpec):
    """
    Specification for a set of manifests to be deployed onto the target cluster.
    """
    manifest_sources: t.List[ManifestSource] = Field(
        default_factory = list,
        description = "The manifest sources for the release."
    )


class Manifests(
    EphemeralChartAddon,
    plural_name = "manifests",
    subresources = {"status": {}},
    printer_columns = [
        {
            "name": "Phase",
            "type": "string",
            "jsonPath": ".status.phase",
        },
        {
            "name": "Revision",
            "type": "integer",
            "jsonPath": ".status.revision",
        },
        {
            "name": "Release Namespace",
            "type": "string",
            "jsonPath": ".spec.targetNamespace",
        },
        {
            "name": "Release Name",
            "type": "string",
            "jsonPath": ".spec.releaseName",
        },
    ]
):
    """
    Addon that deploys manifests.
    """
    spec: ManifestsSpec = Field(
        ...,
        description = "The specification for the manifests."
    )

    async def get_resources(
        self,
        template_loader: Loader,
        ek_client: AsyncClient,
        cluster: t.Dict[str, t.Any],
        infra_cluster: t.Dict[str, t.Any]
    ) -> t.Iterable[t.Dict[str, t.Any]]:
        """
        Returns the resources to use to build the ephemeral chart.
        """
        for source in self.spec.manifest_sources:
            for resource in await source.get_resources(
                template_loader,
                ek_client,
                self,
                cluster,
                infra_cluster
            ):
                yield resource
