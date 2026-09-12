"""APP 档案路由包；保持 ``app.api.app_profiles.router`` 导出兼容。"""
# ruff: noqa: E402,F401,I001

from fastapi import APIRouter

router = APIRouter(tags=["APP 档案"])

from . import profiles as _profiles  # noqa: E402,F401
from . import releases as _releases  # noqa: E402,F401
from . import skip_rules as _skip_rules  # noqa: E402,F401
from . import variables as _variables  # noqa: E402,F401
from . import workspace as _workspace  # noqa: E402,F401

from .profiles import list_app_profiles, create_app_profile, get_app_profile, update_app_profile, delete_app_profile
from .releases import list_releases, create_release, update_release, delete_release
from .skip_rules import skip_rules_batch
from .variables import get_suite_case_variables, patch_suite_case_variables
from .workspace import workspace, workspace_nodes, hub_suite_steps, differences

__all__ = ["router", 'list_app_profiles', 'create_app_profile', 'get_app_profile', 'update_app_profile', 'delete_app_profile', 'list_releases', 'create_release', 'update_release', 'delete_release', 'skip_rules_batch', 'get_suite_case_variables', 'patch_suite_case_variables', 'workspace', 'workspace_nodes', 'hub_suite_steps', 'differences']
