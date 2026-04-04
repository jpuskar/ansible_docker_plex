
# Prep windows
Export CA cert frmo harbor and import into windows trusted roots
docker login harbor.spaceckippy.net
username: harbor_registry_user
password: from harbor-admin-secret secret

# Access denied
Exec into postgres pods and delete harbor admin password:

```shell
psql
```

Then:
```postgresql
\c registry
UPDATE harbor.harbor_user SET password='', salt='' WHERE user_id=1;
```

Then restart harbor pods.
