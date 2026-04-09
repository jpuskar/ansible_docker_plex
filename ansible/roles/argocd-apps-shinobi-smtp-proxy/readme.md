
Getting discord token from mysql:
```shell
mysql -h localhost -u majesticflame -p
```
```sql
use ccio;
SELECT ke, details ->>'$.discordbot_token' AS token, details->>'$.discordbot_channel' AS channel FROM Users;
```

Getting api keys from mysql:

```shell
mysql -h localhost -u majesticflame -p
```
```sql
use ccio;

```
