"""LDAP authentication backend using ldap3.

Provides:
- LDAP bind authentication
- User attribute retrieval (uid, cn, mail, memberOf)
- Lazy provisioning: sync LDAP users to adh_users on first login
- LDAP group → local role mapping
- User/group search for admin UI
"""
from __future__ import annotations

import logging
import time as _time
from datetime import datetime
from typing import Optional

from services.shared.common.config import (
    LDAP_ENABLED, LDAP_SERVER_URL, LDAP_USE_SSL, LDAP_STARTTLS,
    LDAP_BASE_DN, LDAP_BIND_DN, LDAP_BIND_PASSWORD,
    LDAP_USER_SEARCH_BASE, LDAP_USER_SEARCH_FILTER,
    LDAP_GROUP_SEARCH_BASE, LDAP_GROUP_SEARCH_FILTER,
    LDAP_USER_ATTR_USERNAME, LDAP_USER_ATTR_EMAIL, LDAP_USER_ATTR_CN,
    LDAP_CONNECT_TIMEOUT, LDAP_DEFAULT_ROLE,
)
from services.shared.common.db import DBConnection
from services.shared.common.crypto import encrypt_password

logger = logging.getLogger(__name__)

# Conditional import — ldap3 is only needed when LDAP is enabled
_ldap3 = None


def _get_ldap3():
    """Lazy import ldap3 to avoid hard dependency."""
    global _ldap3
    if _ldap3 is None:
        try:
            import ldap3
            _ldap3 = ldap3
        except ImportError:
            raise ImportError(
                "ldap3 is required for LDAP authentication. "
                "Install it with: pip install ldap3"
            )
    return _ldap3


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ts_id() -> int:
    return int(_time.time() * 1000)


class LDAPBackend:
    """LDAP authentication backend.

    Usage:
        backend = LDAPBackend()
        result = backend.authenticate("zhangsan", "password123")
        if result:
            user = backend.sync_user_to_local(result)
    """

    def __init__(self):
        if not LDAP_ENABLED:
            return
        self._server_url = LDAP_SERVER_URL
        self._use_ssl = LDAP_USE_SSL
        self._starttls = LDAP_STARTTLS
        self._base_dn = LDAP_BASE_DN
        self._bind_dn = LDAP_BIND_DN
        self._bind_password = LDAP_BIND_PASSWORD
        self._user_search_base = f"{LDAP_USER_SEARCH_BASE},{LDAP_BASE_DN}" \
            if LDAP_USER_SEARCH_BASE and not LDAP_USER_SEARCH_BASE.endswith(LDAP_BASE_DN) \
            else LDAP_USER_SEARCH_BASE or LDAP_BASE_DN
        self._user_filter = LDAP_USER_SEARCH_FILTER
        self._group_search_base = f"{LDAP_GROUP_SEARCH_BASE},{LDAP_BASE_DN}" \
            if LDAP_GROUP_SEARCH_BASE and not LDAP_GROUP_SEARCH_BASE.endswith(LDAP_BASE_DN) \
            else LDAP_GROUP_SEARCH_BASE or LDAP_BASE_DN
        self._group_filter = LDAP_GROUP_SEARCH_FILTER
        self._attr_username = LDAP_USER_ATTR_USERNAME
        self._attr_email = LDAP_USER_ATTR_EMAIL
        self._attr_cn = LDAP_USER_ATTR_CN
        self._timeout = LDAP_CONNECT_TIMEOUT
        self._default_role = LDAP_DEFAULT_ROLE

    def _create_connection(self, user_dn: str = None, password: str = None):
        """Create an LDAP connection.

        If user_dn/password are provided, binds as that user (for auth).
        Otherwise binds as the service account (for searches).
        """
        ldap3 = _get_ldap3()
        server = ldap3.Server(
            self._server_url,
            use_ssl=self._use_ssl,
            connect_timeout=self._timeout,
            get_info=ldap3.ALL,
        )
        conn = ldap3.Connection(
            server,
            user=user_dn or self._bind_dn,
            password=password or self._bind_password,
            auto_bind=True if not self._starttls else ldap3.AUTO_BIND_TLS_BEFORE_BIND,
            receive_timeout=self._timeout,
        )
        return conn

    def authenticate(self, username: str, password: str) -> Optional[dict]:
        """Authenticate a user against LDAP.

        Steps:
        1. Bind as service account
        2. Search for the user by username
        3. Attempt to bind as the found user DN with the provided password
        4. Return user attributes on success

        Returns:
            dict with keys: dn, username, email, cn, groups
            None on authentication failure
        """
        if not LDAP_ENABLED:
            return None

        if not password:
            return None

        try:
            # Step 1: Bind as service account to search for user
            conn = self._create_connection()

            # Step 2: Search for user
            search_filter = self._user_filter.replace("{username}", username)
            search_base = self._user_search_base

            conn.search(
                search_base=search_base,
                search_filter=search_filter,
                attributes=[self._attr_username, self._attr_email, self._attr_cn, "memberOf"],
            )

            if not conn.entries:
                logger.info("LDAP user not found: %s", username)
                conn.unbind()
                return None

            user_entry = conn.entries[0]
            user_dn = str(user_entry.entry_dn)

            # Extract attributes
            ldap_username = str(getattr(user_entry, self._attr_username, username)) if hasattr(user_entry, self._attr_username) else username
            ldap_email = str(getattr(user_entry, self._attr_email, "")) if hasattr(user_entry, self._attr_email) else ""
            ldap_cn = str(getattr(user_entry, self._attr_cn, "")) if hasattr(user_entry, self._attr_cn) else ""
            member_of = [str(g) for g in getattr(user_entry, "memberOf", [])]

            conn.unbind()

            # Step 3: Bind as the user to verify password
            try:
                user_conn = self._create_connection(user_dn=user_dn, password=password)
                user_conn.unbind()
            except Exception:
                logger.info("LDAP authentication failed for user: %s (invalid password)", username)
                return None

            logger.info("LDAP authentication successful for user: %s", username)

            return {
                "dn": user_dn,
                "username": ldap_username,
                "email": ldap_email,
                "cn": ldap_cn,
                "groups": member_of,
            }

        except Exception as e:
            logger.error("LDAP authentication error for user %s: %s", username, e)
            return None

    def sync_user_to_local(self, ldap_user: dict) -> dict:
        """Sync an LDAP user to adh_users table (lazy provisioning).

        If the user already exists (matched by username), update LDAP fields.
        If not, create a new user with auth_source='ldap'.

        Returns:
            dict with keys: id, username, role, status
        """
        username = ldap_user["username"]
        email = ldap_user.get("email", "")
        cn = ldap_user.get("cn", "")
        dn = ldap_user.get("dn", "")
        groups = ldap_user.get("groups", [])

        try:
            with DBConnection() as conn:
                with conn.cursor() as cur:
                    # Check if user exists
                    cur.execute(
                        "SELECT id, username, user_role, status FROM adh_users WHERE username = %s",
                        (username,),
                    )
                    existing = cur.fetchone()

                    if existing:
                        # Update LDAP fields on existing user
                        cur.execute(
                            "UPDATE adh_users SET ldap_dn = %s, ldap_sync_time = %s, "
                            "auth_source = 'ldap', updated_at = %s WHERE id = %s",
                            (dn, _now_str(), _now_str(), existing["id"]),
                        )
                        conn.commit()
                        logger.info("Updated LDAP sync for existing user: %s", username)
                        return {
                            "id": existing["id"],
                            "username": existing["username"],
                            "role": existing["user_role"],
                            "status": existing["status"],
                        }

                    # Create new user
                    user_id = _ts_id()
                    now = _now_str()

                    # Determine role from LDAP group mapping
                    role = self._map_ldap_groups_to_role(groups)

                    # Generate a random password hash for LDAP users (they auth via LDAP)
                    # This prevents local login with a known password
                    import bcrypt
                    random_hash = bcrypt.hashpw(
                        f"ldap-{user_id}".encode("utf-8"),
                        bcrypt.gensalt(),
                    ).decode("utf-8")

                    cur.execute(
                        "INSERT INTO adh_users "
                        "(id, username, password_hash, email, phone, avatar, user_role, status, "
                        "login_attempts, auth_source, ldap_dn, ldap_sync_time, created_at, updated_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (user_id, username, random_hash, email, "", "", role, "active",
                         0, "ldap", dn, now, now, now),
                    )

                    # Auto-add to default workspace
                    cur.execute("SELECT id FROM adh_workspaces WHERE is_default = 1 LIMIT 1")
                    default_ws = cur.fetchone()
                    if default_ws:
                        cur.execute(
                            """INSERT IGNORE INTO adh_workspace_users (workspace_id, user_id, role, is_default)
                               VALUES (%s, %s, 'member', 1)""",
                            (default_ws["id"], user_id),
                        )

                    conn.commit()
                    logger.info("Created new LDAP user: %s (role=%s)", username, role)
                    return {
                        "id": user_id,
                        "username": username,
                        "role": role,
                        "status": "active",
                    }

        except Exception as e:
            logger.error("Failed to sync LDAP user %s: %s", username, e)
            # Fallback: return minimal info
            return {
                "id": 0,
                "username": username,
                "role": self._default_role,
                "status": "active",
            }

    def _map_ldap_groups_to_role(self, groups: list[str]) -> str:
        """Map LDAP group DNs to a local role using adh_ldap_role_mapping table.

        If no mapping found, returns the default role.
        """
        if not groups:
            return self._default_role

        try:
            with DBConnection() as conn:
                with conn.cursor() as cur:
                    # Try to find a mapping for any of the user's groups
                    placeholders = ", ".join(["%s"] * len(groups))
                    cur.execute(
                        f"SELECT local_role FROM adh_ldap_role_mapping "
                        f"WHERE ldap_group_dn IN ({placeholders}) "
                        f"ORDER BY id LIMIT 1",
                        groups,
                    )
                    row = cur.fetchone()
                    if row:
                        return row["local_role"]
        except Exception as e:
            logger.warning("Failed to lookup LDAP role mapping: %s", e)

        return self._default_role

    def get_user_groups(self, user_id: int) -> list[str]:
        """Get LDAP group DNs for a user by their user_id.

        Looks up the user's ldap_dn, then queries LDAP for memberOf.
        Returns empty list if user has no LDAP DN or LDAP is disabled.
        """
        if not LDAP_ENABLED:
            return []

        try:
            with DBConnection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT ldap_dn FROM adh_users WHERE id = %s", (user_id,))
                    row = cur.fetchone()
                    if not row or not row.get("ldap_dn"):
                        return []

            # Query LDAP for the user's groups
            ldap_conn = self._create_connection()
            ldap_conn.search(
                search_base=row["ldap_dn"],
                search_filter="(objectClass=*)",
                attributes=["memberOf"],
            )
            if ldap_conn.entries:
                groups = [str(g) for g in getattr(ldap_conn.entries[0], "memberOf", [])]
                ldap_conn.unbind()
                return groups
            ldap_conn.unbind()
        except Exception as e:
            logger.warning("Failed to get LDAP groups for user %d: %s", user_id, e)

        return []

    def search_users(self, keyword: str, limit: int = 20) -> list[dict]:
        """Search LDAP users by keyword (uid or cn).

        Returns list of dicts with: dn, username, email, cn
        """
        if not LDAP_ENABLED:
            return []

        try:
            conn = self._create_connection()
            search_filter = (
                f"(|({self._attr_username}=*{keyword}*)"
                f"({self._attr_cn}=*{keyword}*)"
                f"(mail=*{keyword}*))"
            )
            conn.search(
                search_base=self._user_search_base,
                search_filter=search_filter,
                attributes=[self._attr_username, self._attr_email, self._attr_cn],
                size_limit=limit,
            )

            results = []
            for entry in conn.entries:
                results.append({
                    "dn": str(entry.entry_dn),
                    "username": str(getattr(entry, self._attr_username, "")),
                    "email": str(getattr(entry, self._attr_email, "")),
                    "cn": str(getattr(entry, self._attr_cn, "")),
                })
            conn.unbind()
            return results
        except Exception as e:
            logger.error("LDAP user search failed: %s", e)
            return []

    def search_groups(self, keyword: str = "", limit: int = 50) -> list[dict]:
        """Search LDAP groups.

        Returns list of dicts with: dn, name, description
        """
        if not LDAP_ENABLED:
            return []

        try:
            conn = self._create_connection()
            if keyword:
                search_filter = f"(&(objectClass=groupOfNames)(cn=*{keyword}*))"
            else:
                search_filter = "(objectClass=groupOfNames)"

            conn.search(
                search_base=self._group_search_base,
                search_filter=search_filter,
                attributes=["cn", "description"],
                size_limit=limit,
            )

            results = []
            for entry in conn.entries:
                results.append({
                    "dn": str(entry.entry_dn),
                    "name": str(getattr(entry, "cn", "")),
                    "description": str(getattr(entry, "description", "")),
                })
            conn.unbind()
            return results
        except Exception as e:
            logger.error("LDAP group search failed: %s", e)
            return []


# Singleton instance
ldap_backend = LDAPBackend()
