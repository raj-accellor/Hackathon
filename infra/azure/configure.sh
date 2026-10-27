#!/usr/bin/env bash
# Push backend configuration to Azure: secrets -> Key Vault, everything else -> Container Apps env vars.
# The apps read secrets from Key Vault and pull images from ACR through one user-assigned managed identity,
# so no secret value and no registry password is stored on the apps themselves.
#
# Re-runnable: existing resources are reused, secrets get a new version, env vars are replaced as a set.
#
#   cp .env .env.azure      # then point DATABASE_URL / REDIS_URL / CORS_ORIGINS / TICKERTAPE_REDIRECT_URI at Azure
#   AZ_RG=rg-markets AZ_KV=kv-markets-prod AZ_ACR=acrmarketsprod BUILD_IMAGES=1 infra/azure/configure.sh .env.azure
#
# Optional: AZ_LOCATION (centralindia), AZ_CAE, AZ_IDENTITY, AZ_API_APP, AZ_WORKER_APP, AZ_WEB_APP, IMAGE_TAG (latest),
#           AZ_ASSIGNEE_OBJECT_ID (object id to grant Key Vault write access; defaults to the signed-in user).
# Postgres and Redis are not created here; supply their URLs in the env file.
set -euo pipefail

ENV_FILE=${1:?usage: $0 <env-file>}
RG=${AZ_RG:?set AZ_RG}
KV=${AZ_KV:?set AZ_KV (3-24 chars, globally unique)}
ACR=${AZ_ACR:?set AZ_ACR (alphanumeric, globally unique)}
LOCATION=${AZ_LOCATION:-centralindia}
CAE=${AZ_CAE:-cae-markets}
IDENTITY=${AZ_IDENTITY:-id-markets}
API_APP=${AZ_API_APP:-ca-markets-api}
WORKER_APP=${AZ_WORKER_APP:-ca-markets-worker}
WEB_APP=${AZ_WEB_APP:-ca-markets-web}
TAG=${IMAGE_TAG:-latest}

# Values that go to Key Vault. Everything else becomes a plain env var.
SECRET_KEYS=" DATABASE_URL REDIS_URL LLM_API_KEY WEB_SEARCH_API_KEY API_AUTH_TOKEN FRED_API_KEY "
# Unused by the backend (Kite goes through MCP), local-only, or set per app below.
SKIP_KEYS=" KITE_API_KEY KITE_API_SECRET KITE_ACCESS_TOKEN MOCK_MCP_HOST SCHEDULER_ENABLED "

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarn:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }
has() { [[ $1 == *" $2 "* ]]; }

# ---- 1. Parse the env file (never `source` it: values are data, not shell) --------------------------------
[[ -f $ENV_FILE ]] || die "$ENV_FILE not found"
KEYS=(); VALS=()
re_skip='^[[:space:]]*(#|$)'
re_kv='^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$'
re_dq='^"(.*)"$'
re_sq="^'(.*)'$"
while IFS= read -r line || [[ -n $line ]]; do
  line=${line%$'\r'}
  [[ $line =~ $re_skip ]] && continue
  [[ $line =~ $re_kv ]] || { warn "skipping unparseable line in $ENV_FILE"; continue; }
  key=${BASH_REMATCH[2]}
  # Drop trailing " # comment" (whitespace before #, so "abc#def" survives) and surrounding whitespace/quotes.
  val=$(printf '%s' "${BASH_REMATCH[3]}" | sed -E 's/[[:space:]]+#.*$//; s/^[[:space:]]+//; s/[[:space:]]+$//')
  if [[ $val =~ $re_dq || $val =~ $re_sq ]]; then val=${BASH_REMATCH[1]}; fi
  has "$SKIP_KEYS" "$key" && continue
  [[ -z $val ]] && continue            # empty == unset == the app's default
  KEYS+=("$key"); VALS+=("$val")
done < "$ENV_FILE"

get() { local i; for i in "${!KEYS[@]}"; do [[ ${KEYS[$i]} == "$1" ]] && { printf '%s' "${VALS[$i]}"; return; }; done; }

# ---- 2. Refuse obviously local configuration before touching Azure ----------------------------------------
for k in DATABASE_URL REDIS_URL; do
  v=$(get "$k")
  [[ $v == *localhost* || $v == *127.0.0.1* ]] && die "$k points at localhost; set the Azure value in $ENV_FILE"
done
[[ -z $(get DATABASE_URL) ]] && die "DATABASE_URL is empty"
[[ -z $(get REDIS_URL) ]] && warn "REDIS_URL empty: api and worker run as separate containers and need Redis for shared cache/locks"
for k in CORS_ORIGINS TICKERTAPE_REDIRECT_URI; do
  v=$(get "$k"); [[ $v == *localhost* || $v == *127.0.0.1* ]] && warn "$k still points at localhost"
done
[[ $(get APP_ENV) == production ]] || warn "APP_ENV is '$(get APP_ENV)', not 'production'"

# ---- 3. Azure foundation -----------------------------------------------------------------------------------
az account show -o none 2>/dev/null || die "run 'az login' first"
az extension add --name containerapp --upgrade --only-show-errors -o none
az provider register -n Microsoft.App --wait -o none
az provider register -n Microsoft.OperationalInsights --wait -o none
log "subscription: $(az account show --query name -o tsv)"

az group create -n "$RG" -l "$LOCATION" -o none

assign_role() {  # object-id principal-type role scope
  if [[ -z $(az role assignment list --assignee "$1" --role "$3" --scope "$4" --query '[0].id' -o tsv) ]]; then
    az role assignment create --assignee-object-id "$1" --assignee-principal-type "$2" --role "$3" --scope "$4" -o none
  fi
}

log "Key Vault $KV"
az keyvault show -n "$KV" -g "$RG" -o none 2>/dev/null \
  || az keyvault create -n "$KV" -g "$RG" -l "$LOCATION" --enable-rbac-authorization true -o none
KV_ID=$(az keyvault show -n "$KV" -g "$RG" --query id -o tsv)
KV_URI=$(az keyvault show -n "$KV" -g "$RG" --query properties.vaultUri -o tsv)
ME=${AZ_ASSIGNEE_OBJECT_ID:-$(az ad signed-in-user show --query id -o tsv)}
assign_role "$ME" User "Key Vault Secrets Officer" "$KV_ID"

log "Container Registry $ACR"
az acr show -n "$ACR" -g "$RG" -o none 2>/dev/null \
  || az acr create -n "$ACR" -g "$RG" -l "$LOCATION" --sku Basic --admin-enabled false -o none
ACR_ID=$(az acr show -n "$ACR" -g "$RG" --query id -o tsv)
ACR_SERVER=$(az acr show -n "$ACR" -g "$RG" --query loginServer -o tsv)

log "Managed identity $IDENTITY"
az identity show -n "$IDENTITY" -g "$RG" -o none 2>/dev/null || az identity create -n "$IDENTITY" -g "$RG" -l "$LOCATION" -o none
ID_RESOURCE=$(az identity show -n "$IDENTITY" -g "$RG" --query id -o tsv)
ID_PRINCIPAL=$(az identity show -n "$IDENTITY" -g "$RG" --query principalId -o tsv)
assign_role "$ID_PRINCIPAL" ServicePrincipal "Key Vault Secrets User" "$KV_ID"
assign_role "$ID_PRINCIPAL" ServicePrincipal "AcrPull" "$ACR_ID"

# ---- 4. Secrets -> Key Vault; plain values -> env var list -------------------------------------------------
log "Writing secrets to Key Vault"
ENV_ARGS=(); APP_SECRETS=()
tmp=$(mktemp); chmod 600 "$tmp"; trap 'rm -f "$tmp"' EXIT
for i in "${!KEYS[@]}"; do
  key=${KEYS[$i]}; val=${VALS[$i]}
  if has "$SECRET_KEYS" "$key"; then
    name=$(printf '%s' "$key" | tr '[:upper:]_' '[:lower:]-')     # Key Vault names allow only [0-9a-zA-Z-]
    printf '%s' "$val" > "$tmp"                                   # via file so the value never hits argv / ps
    for attempt in 1 2 3 4 5 6; do                                # new RBAC grants take a minute to propagate
      az keyvault secret set --vault-name "$KV" -n "$name" --file "$tmp" --encoding utf-8 -o none 2>/dev/null && break
      [[ $attempt == 6 ]] && die "could not write secret $name (check Key Vault RBAC)"
      warn "Key Vault write denied, retrying in 20s (RBAC propagation)"; sleep 20
    done
    echo "   $key -> $name"
    # Versionless URI: the app picks up rotated values on its next revision/restart.
    APP_SECRETS+=("$name=keyvaultref:${KV_URI%/}/secrets/$name,identityref:$ID_RESOURCE")
    ENV_ARGS+=("$key=secretref:$name")
  else
    ENV_ARGS+=("$key=$val")
  fi
done
: > "$tmp"
log "${#APP_SECRETS[@]} secrets, $(( ${#ENV_ARGS[@]} - ${#APP_SECRETS[@]} )) plain env vars"

# ---- 5. Images ---------------------------------------------------------------------------------------------
cd "$(git rev-parse --show-toplevel)"
if [[ ${BUILD_IMAGES:-0} == 1 ]]; then
  log "Building images in ACR (tag $TAG)"   # .dockerignore keeps .env* out of the uploaded context
  az acr build -r "$ACR" -t "markets-backend:$TAG" -f infra/docker/backend.Dockerfile . -o none
  az acr build -r "$ACR" -t "markets-web:$TAG" -f infra/docker/frontend.Dockerfile . -o none
fi

# ---- 6. Container Apps -------------------------------------------------------------------------------------
log "Container Apps environment $CAE"
az containerapp env show -n "$CAE" -g "$RG" -o none 2>/dev/null \
  || az containerapp env create -n "$CAE" -g "$RG" -l "$LOCATION" -o none

ensure_app() {  # name image [extra create args...]
  local name=$1 image=$2; shift 2
  if az containerapp show -n "$name" -g "$RG" -o none 2>/dev/null; then
    az containerapp identity assign -n "$name" -g "$RG" --user-assigned "$ID_RESOURCE" -o none
    az containerapp registry set -n "$name" -g "$RG" --server "$ACR_SERVER" --identity "$ID_RESOURCE" -o none
  else
    log "creating $name"
    az containerapp create -n "$name" -g "$RG" --environment "$CAE" --image "$image" \
      --user-assigned "$ID_RESOURCE" --registry-server "$ACR_SERVER" --registry-identity "$ID_RESOURCE" "$@" -o none
  fi
}

configure_backend() {  # name per-app-env...
  local name=$1; shift
  log "configuring $name"
  az containerapp secret set -n "$name" -g "$RG" --secrets "${APP_SECRETS[@]}" -o none
  az containerapp update -n "$name" -g "$RG" --image "$ACR_SERVER/markets-backend:$TAG" \
    --replace-env-vars "${ENV_ARGS[@]}" "$@" -o none
}

BACKEND_IMAGE="$ACR_SERVER/markets-backend:$TAG"

# API: internal ingress only; the web app proxies /api to it. Scheduler off so jobs run once, in the worker.
ensure_app "$API_APP" "$BACKEND_IMAGE" --ingress internal --target-port 8000 --min-replicas 1 --max-replicas 3
configure_backend "$API_APP" SCHEDULER_ENABLED=false

# Worker: no ingress, exactly one replica (it owns the scheduler). Started as a script path because the
# CLI would parse "-m" as its own flag; PYTHONPATH makes the `app` package importable from there.
ensure_app "$WORKER_APP" "$BACKEND_IMAGE" --min-replicas 1 --max-replicas 1 \
  --command /app/.venv/bin/python --args /app/app/workers/run.py
configure_backend "$WORKER_APP" SCHEDULER_ENABLED=true PYTHONPATH=/app

# Web: public ingress; nginx proxies to the API over the environment's internal DNS.
ensure_app "$WEB_APP" "$ACR_SERVER/markets-web:$TAG" --ingress external --target-port 8080 --min-replicas 1 --max-replicas 2
az containerapp update -n "$WEB_APP" -g "$RG" --image "$ACR_SERVER/markets-web:$TAG" \
  --replace-env-vars "API_UPSTREAM=$API_APP" -o none

log "done: https://$(az containerapp show -n "$WEB_APP" -g "$RG" --query properties.configuration.ingress.fqdn -o tsv)"
