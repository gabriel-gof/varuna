from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from topology.api.auth_utils import can_modify_settings, resolve_user_role


@api_view(['POST'])
@permission_classes([AllowAny])
def login_view(request):
    username = request.data.get('username', '').strip()
    password = request.data.get('password', '')

    if not username or not password:
        return Response(
            {'detail': 'Username and password are required.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user = authenticate(request, username=username, password=password)
    if user is None:
        return Response(
            {'detail': 'Invalid credentials.'},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    token, _ = Token.objects.get_or_create(user=user)
    return Response({
        'token': token.key,
        'user': {
            'id': user.id,
            'username': user.username,
            'role': resolve_user_role(user),
            'can_modify_settings': can_modify_settings(user),
        },
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def logout_view(request):
    if hasattr(request.user, 'auth_token'):
        request.user.auth_token.delete()
    return Response({'detail': 'Logged out.'})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def me_view(request):
    role = resolve_user_role(request.user)
    return Response({
        'id': request.user.id,
        'username': request.user.username,
        'role': role,
        'can_modify_settings': can_modify_settings(request.user),
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def change_password_view(request):
    current_password = request.data.get('current_password', '')
    new_password = request.data.get('new_password', '')

    if not current_password or not new_password:
        return Response(
            {'detail': 'Current and new password are required.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user = request.user
    if not user.check_password(current_password):
        return Response(
            {'detail': 'Current password is incorrect.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if current_password == new_password:
        return Response(
            {'detail': 'New password must be different from current password.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        validate_password(new_password, user=user)
    except ValidationError as exc:
        return Response(
            {
                'detail': 'New password does not meet policy requirements.',
                'errors': list(exc.messages),
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    user.set_password(new_password)
    user.save(update_fields=['password'])

    # Rotate token after password change so previous credentials cannot be reused.
    Token.objects.filter(user=user).delete()
    token = Token.objects.create(user=user)

    return Response(
        {
            'detail': 'Password updated.',
            'token': token.key,
        }
    )
