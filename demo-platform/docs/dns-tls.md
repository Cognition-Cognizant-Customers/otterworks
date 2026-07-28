# AWS-native DNS + wildcard TLS

This is the self-contained, AWS-only replacement for the temporary **nip.io**
hostnames. It removes the last non-AWS / IP-based dependency from the demo
platform.

## Why not nip.io

`nip.io` is a free **third-party** wildcard DNS service: `anything.<IP>.nip.io`
resolves to the IP embedded in the name. It let us demo host-based routing with
zero DNS setup, but it is unacceptable for rollout:

- **Not AWS** — an outside dependency in the request path.
- **IP-pinned** — the hostname hard-codes an NLB IP; NLB IPs change on
  AZ failover/recreate and every URL breaks at once.
- **No real TLS** — you cannot get a browser-trusted wildcard cert for a domain
  you don't control, so it is HTTP-only.
- **Blocked on many networks** — corporate/guest DNS often filters `*.nip.io`.

## Target (everything inside AWS)

```
Route53 (registered otterworks.app)
  └─ hosted zone otterworks.app
       ├─ external-dns  ──> writes t-<id>.demo.otterworks.app / api-t-<id>.demo…  ->  shared NLB
       └─ cert-manager (ACME DNS-01 over Route53) ──> ONE wildcard cert *.demo.otterworks.app (+ ops.otterworks.app)
                                                          └─ ingress-nginx --default-ssl-certificate  ->  HTTPS for every tenant
```

- **DNS: Route53.** One public hosted zone for `otterworks.app`; `external-dns`
  reconciles a record per Ingress automatically and deletes it when the namespace
  is removed. Its `--domain-filter` covers both the tenant subdomain
  (`demo.otterworks.app`) and the dashboard host (`ops.otterworks.app`, a sibling
  of `demo.`), so both resolve with zero per-tenant DNS work and no IPs in any URL.
- **TLS: one wildcard cert.** `cert-manager` solves an ACME **DNS-01** challenge
  through Route53 (DNS-01 is required for a wildcard) and stores the cert as a
  Secret in `ingress-nginx`, wired as the controller's
  `--default-ssl-certificate`. Every current and future tenant serves HTTPS with
  no extra certs, and ingress-nginx performs the HTTP→HTTPS redirect globally —
  so no per-ingress `ssl-redirect` annotation is needed, and pre-TLS / nip.io
  deployments (before the default cert is wired) keep serving over plain HTTP.
- **CA note.** The wildcard cert is issued by **Let's Encrypt** (free, standard,
  automated). DNS and validation are 100% inside AWS/Route53; only the CA is
  external — this is the normal pattern for cert-manager on EKS. If the CA itself
  must be AWS, the alternative is **ACM + an ALB ingress** (AWS Private/Public CA
  or ACM public cert); that swaps ingress-nginx/NLB for the AWS Load Balancer
  Controller and is a larger change — documented here as the option, not the
  default.

## IAM (already in Terraform, gated)

`infra/terraform/dns.tf` creates the hosted zone and a single IRSA role
(`otterworks-demo-dns-<env>`) trusted by both the `external-dns` and
`cert-manager` ServiceAccounts, scoped to `route53:ChangeResourceRecordSets` on
that zone. The hosted zone itself is a data lookup, never managed here: it carries the
registrar's NS delegation and must outlive any rebuild of the platform. `enable_dns`
defaults on and gates only the IRSA role -- turning it off (or applying with it unset,
as it once defaulted) deletes the role external-dns and cert-manager assume, which stops
new tenant records and certificate renewal while existing records still resolve. It was
gated off originally so nothing was created until
the domain exists.

## Rollout steps

1. **Register the domain** (the one manual, out-of-band step — needs a real
   registrant contact + ICANN verification; ~$20/yr for `.app`, which is
   HSTS-preloaded so it is HTTPS-only in browsers):

   ```bash
   aws route53domains register-domain --region us-east-1 \
     --domain-name otterworks.app --duration-in-years 1 \
     --admin-contact file://contact.json --registrant-contact file://contact.json \
     --tech-contact file://contact.json --privacy-protect-admin-contact \
     --privacy-protect-registrant-contact --privacy-protect-tech-contact
   ```

2. **Create the zone + DNS IAM:**

   ```bash
   terraform -chdir=demo-platform/infra/terraform apply
   ```

   If the domain was registered *outside* Route53, point its registrar
   nameservers at `terraform output dns_zone_name_servers`.

3. **Turn on external-dns + cert-manager + wildcard TLS** (idempotent):

   ```bash
   DOMAIN_ROOT=otterworks.app ACME_EMAIL=<platform-contact-email> \
     ISSUER=letsencrypt-staging scripts/enable-dns-tls.sh
   # once the staging cert is Ready:
   DOMAIN_ROOT=otterworks.app ACME_EMAIL=<platform-contact-email> \
     ISSUER=letsencrypt-prod scripts/enable-dns-tls.sh
   ```

4. **Done.** Deploy/checkout tenants normally — the scripts, runner, reaper and
   dashboard already default to `--host-suffix demo.otterworks.app`, so tenants
   come up at `https://t-<id>.demo.otterworks.app` and the dashboard at
   `https://ops.otterworks.app`. The reaper's `gc_route53` cleans any stray
   records belonging to expired tenants.

## Verify

```bash
kubectl -n external-dns logs deploy/external-dns --tail=20        # record syncs
kubectl -n ingress-nginx get certificate otterworks-wildcard      # READY=True
dig +short t-<id>.demo.otterworks.app                             # -> NLB
curl -sI https://t-<id>.demo.otterworks.app | head -1            # HTTP/2 200
```
