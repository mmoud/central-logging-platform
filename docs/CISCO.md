# Cisco IOS, IOS-XE, NX-OS and Nexus

Map every management/loopback sender IP as `cisco`. Prefer `logging host COLLECTOR transport tcp port 514`; use a stable loopback with `logging source-interface` and set a distinct facility such as `local0` where appropriate. Modern IOS-XE supports `logging host … transport tls port 6514 profile …`; exact TLS/trustpoint syntax varies by release and platform.

```text
logging facility local0
logging origin-id hostname
logging source-interface Loopback0
logging trap informational
logging host 192.0.2.10 transport tcp port 514
```

The collector retains facility/severity, program, full raw message, and source IP. Cisco identifiers such as `%LINK-3-UPDOWN`, `%LINEPROTO-5-UPDOWN`, `%SEC_LOGIN`, `%OSPF`, `%BGP`, `%ETHPORT`, and `%VPC` remain searchable in `message`/`raw_message`; build targeted saved searches from those stable message classes rather than using hundreds of brittle expressions.
