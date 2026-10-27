# Generate and synchronize Keycloak client credentials

Date: 2026-10-27

The source scan found confidential-client secrets committed in the realm export
and the frontend environment example. These values authenticate real local
clients, even when the application is a local demo.

Replace the ten realm client secrets with environment substitutions. Generate
their private values in the ignored bootstrap .env, copy the web client's value
to the ignored frontend environment, and store the values in Vault at bootstrap.
No credentials are written into tracked templates or printed by the setup tools.

An existing Keycloak database ignores a repeated realm import. Therefore the
local startup task also synchronizes the generated secrets through Keycloak's
authenticated admin API. This updates the existing realm and revokes the old
committed client values without deleting customer data or resetting volumes.

The generator merges newly introduced template keys into existing environment
files and preserves existing generated values on subsequent runs. The frontend
client secret is synchronized from the bootstrap value so it cannot drift from
the realm import. Restart the frontend after a credential rotation.
