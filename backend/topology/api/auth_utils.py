from topology.models import UserProfile


def resolve_user_role(user) -> str:
    """
    Resolve role from UserProfile, with safe fallback for superusers.
    """
    if not getattr(user, 'is_authenticated', False):
        return UserProfile.ROLE_VIEWER

    if getattr(user, 'is_superuser', False):
        return UserProfile.ROLE_ADMIN

    profile = getattr(user, 'profile', None)
    if profile and profile.role in {
        UserProfile.ROLE_ADMIN,
        UserProfile.ROLE_OPERATOR,
        UserProfile.ROLE_VIEWER,
    }:
        return profile.role

    return UserProfile.ROLE_VIEWER


def can_modify_settings(user) -> bool:
    role = resolve_user_role(user)
    return role == UserProfile.ROLE_ADMIN


def can_operate_topology(user) -> bool:
    role = resolve_user_role(user)
    return role in {
        UserProfile.ROLE_ADMIN,
        UserProfile.ROLE_OPERATOR,
    }
