# Service Alpha — effective configuration (excerpt from deployed config)

```text
# alpha/config/production.conf
requestTimeoutMs   = 27000
maxRetries         = 3
retryBackoff       = exponential
retryBackoffBaseMs = 250
MAX_QUEUE_DEPTH    = 1250
tlsMinVersion      = 1.3
enableLegacyAuth   = true
logLevel           = info
dbPoolSizePerReplica = 32
```

Deployment notes: values above are read at boot; there is no runtime override
layer in Alpha. The legacy auth flag was toggled during the March incident
bridge and has not been revisited since.
