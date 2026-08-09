"""Kerberos SPNEGO authentication backend.

Provides:
- SPNEGO token validation for browser SSO
- Principal → username mapping
- Keytab-based service authentication

Usage:
    backend = KerberosBackend()
    user_info = backend.validate_spnego_token(spnego_token_bytes)
    if user_info:
        username = backend.principal_to_username(user_info["principal"])

Requirements:
    pip install gssapi
"""
from __future__ import annotations

import logging
from typing import Optional

from services.shared.common.config import (
    KERBEROS_ENABLED, KERBEROS_KEYTAB_PATH,
    KERBEROS_SERVICE_PRINCIPAL, KERBEROS_REALM,
)

logger = logging.getLogger(__name__)

# Conditional import — gssapi is only needed when Kerberos is enabled
_gssapi = None


def _get_gssapi():
    """Lazy import gssapi to avoid hard dependency."""
    global _gssapi
    if _gssapi is None:
        try:
            import gssapi
            _gssapi = gssapi
        except ImportError:
            raise ImportError(
                "gssapi is required for Kerberos authentication. "
                "Install it with: pip install gssapi"
            )
    return _gssapi


class KerberosBackend:
    """Kerberos SPNEGO authentication backend.

    Validates SPNEGO tokens from HTTP requests and extracts user identity.
    Used for browser SSO in enterprise environments with Active Directory.
    """

    def __init__(self):
        if not KERBEROS_ENABLED:
            return
        self._keytab_path = KERBEROS_KEYTAB_PATH
        self._service_principal = KERBEROS_SERVICE_PRINCIPAL
        self._realm = KERBEROS_REALM
        self._server_credentials = None

    def _get_server_credentials(self):
        """Get or create server credentials from keytab."""
        gssapi = _get_gssapi()

        if self._server_credentials is None:
            if not self._keytab_path:
                raise ValueError("KERBEROS_KEYTAB_PATH is not configured")

            # Set KRB5_KTNAME environment variable for keytab
            import os
            os.environ["KRB5_KTNAME"] = self._keytab_path

            # Create server name
            server_name = gssapi.Name(
                self._service_principal,
                name_type=gssapi.NameType.hostbased_service,
            )

            # Acquire credentials from keytab
            self._server_credentials = gssapi.Credentials(
                name=server_name,
                usage="accept",
            )

        return self._server_credentials

    def validate_spnego_token(self, spnego_token: bytes) -> Optional[dict]:
        """Validate a SPNEGO token from an HTTP request.

        This is typically called with the base64-decoded content of the
        "Authorization: Negotiate <token>" header.

        Args:
            spnego_token: Raw SPNEGO/GSSAPI token bytes

        Returns:
            dict with keys: principal, username, realm
            None on validation failure
        """
        if not KERBEROS_ENABLED:
            return None

        if not spnego_token:
            return None

        try:
            gssapi = _get_gssapi()

            # Get server credentials
            server_creds = self._get_server_credentials()

            # Create security context and accept the token
            security_context = gssapi.SecurityContext(
                creds=server_ccreds,
                usage="accept",
            )

            # Accept the SPNEGO token
            output_token = security_context.step(spnego_token)

            # Extract client identity
            initiator_name = security_context.initiator_name
            principal = str(initiator_name)

            # Parse principal: user@REALM
            username = self.principal_to_username(principal)
            realm = self._extract_realm(principal)

            logger.info("Kerberos authentication successful: %s", principal)

            return {
                "principal": principal,
                "username": username,
                "realm": realm,
                "output_token": output_token,  # For mutual authentication
            }

        except Exception as e:
            logger.warning("Kerberos SPNEGO validation failed: %s", e)
            return None

    def principal_to_username(self, principal: str) -> str:
        """Convert a Kerberos principal to a username.

        Examples:
            "user@EXAMPLE.COM" → "user"
            "user/admin@EXAMPLE.COM" → "user"
            "HTTP/server.example.com@EXAMPLE.COM" → "HTTP/server.example.com"
        """
        if "@" not in principal:
            return principal

        name_part = principal.split("@")[0]

        # For service principals (HTTP/...), return as-is
        if "/" in name_part and name_part.startswith(("HTTP/", "host/")):
            return name_part

        # For user principals, take the first part before /
        if "/" in name_part:
            return name_part.split("/")[0]

        return name_part

    def _extract_realm(self, principal: str) -> str:
        """Extract realm from a principal."""
        if "@" in principal:
            return principal.split("@")[-1]
        return self._realm

    def create_service_ticket(self, target_principal: str) -> Optional[bytes]:
        """Create a service ticket for authenticating to another service.

        Used for service-to-service authentication.
        """
        if not KERBEROS_ENABLED:
            return None

        try:
            gssapi = _get_gssapi()

            server_creds = self._get_server_credentials()

            target_name = gssapi.Name(
                target_principal,
                name_type=gssapi.NameType.hostbased_service,
            )

            # Acquire credentials for the target service
            client_creds = gssapi.Credentials(
                name=server_ccreds.name,
                usage="initiate",
            )

            # Create security context for the target
            context = gssapi.SecurityContext(
                creds=client_ccreds,
                name=target_name,
                usage="initiate",
            )

            # Generate token
            token = context.step()
            return token

        except Exception as e:
            logger.error("Failed to create service ticket: %s", e)
            return None


# Singleton instance
kerberos_backend = KerberosBackend()
