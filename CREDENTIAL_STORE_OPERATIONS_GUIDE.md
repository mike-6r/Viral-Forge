# Credential store operations guide

## Backends

`env://NAME` is read-only and is suitable only for pre-provisioned credentials, such as the TikTok client secret. It cannot support OAuth token creation, refresh, or disconnect deletion.

Use the encrypted single-VPS backend for dynamic TikTok OAuth credentials:

```dotenv
VIRALFORGE_CREDENTIAL_STORE_BACKEND=encrypted_file
VIRALFORGE_CREDENTIAL_STORE_FILE_PATH=/data/credentials/viralforge-credentials.json
VIRALFORGE_CREDENTIAL_STORE_MASTER_KEY_REFERENCE=env://VIRALFORGE_CREDENTIAL_STORE_MASTER_KEY
```

`VIRALFORGE_CREDENTIAL_STORE_MASTER_KEY` must contain a Fernet-compatible key supplied by the VPS environment or mounted secret. Do not place it in Git, Discord, the database, or an ordinary application configuration record.

Generate one on the VPS without printing it into shell history or logs:

```bash
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Paste that value directly into the protected production environment file as `VIRALFORGE_CREDENTIAL_STORE_MASTER_KEY=...`, then set the reference above. Restrict the environment file to the deployment operator.

## Lifecycle

- OAuth creates a random opaque `file://` reference. Only that reference is stored on the destination account.
- The encrypted payload has `version: 1` and carries the access token, refresh token, scopes, identity, and expiry only inside the encrypted file.
- Refresh holds a destination database row lock and replaces the entire encrypted payload only after TikTok returns a new valid token response.
- Disconnect calls TikTok revocation, deletes the external payload, then deactivates the destination. If revocation/deletion fails, the destination is marked degraded instead of claiming disconnect success.
- File corruption fails closed. Restore the encrypted file and its matching master key from protected backup; do not hand-edit ciphertext.

## Safe checks

Run these checks from the app container. They report backend health only and never print payloads:

```bash
python -c 'from app.common.config import get_settings; from app.publishing.credentials import credential_store; print(credential_store(get_settings()).health())'
```

Do not use `cat`, `strings`, or log commands against the credential-store file.
