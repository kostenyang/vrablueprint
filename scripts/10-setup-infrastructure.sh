#!/usr/bin/env bash
# Build everything a blueprint needs BEFORE it can deploy:
#   Cloud Account (vCenter) -> Cloud Zone -> Project -> Flavor/Image/Network/Storage profiles -> static IP range
# Idempotency is NOT handled here for brevity - run once on a fresh vRA.
#
# Usage:  source env.sh && ./10-setup-infrastructure.sh
set -euo pipefail
cd "$(dirname "$0")"
source ./lib.sh
export TOK=$(vra_token)
[ -n "$TOK" ] || { echo "failed to get vRA token - check VRA_URL/VRA_USER/VRA_PASS"; exit 1; }
jqid(){ grep -oE '"id":"[^"]*"' | head -1 | cut -d'"' -f4; }

echo "==> 1. Region enumeration (discover the vCenter datacenter)"
REGION_EXT=$(vpost "/iaas/api/cloud-accounts/region-enumeration" \
  "{\"cloudAccountType\":\"vsphere\",\"privateKeyId\":\"$VC_USER\",\"privateKey\":\"$VC_PASS\",\"cloudAccountProperties\":{\"hostName\":\"$VC_HOST\",\"acceptSelfSignedCertificate\":\"true\",\"dcId\":\"onprem\"}}" \
  | grep -oE 'Datacenter:[a-zA-Z0-9-]+' | head -1)
echo "    region = $REGION_EXT"

echo "==> 2. Create the vSphere Cloud Account (auto-creates a Cloud Zone)"
vpost "/iaas/api/cloud-accounts-vsphere" \
  "{\"name\":\"vc-${PROJECT_NAME}\",\"hostName\":\"$VC_HOST\",\"username\":\"$VC_USER\",\"password\":\"$VC_PASS\",\"acceptSelfSignedCertificate\":true,\"regionIds\":[\"$REGION_EXT\"],\"createDefaultZones\":true}" >/dev/null
sleep 8   # let the initial inventory collection start

REGION_ID=$(vget "/iaas/api/regions" | sed 's/},{/}\n{/g' | grep -F "\"externalRegionId\":\"$REGION_EXT\"" | jqid)
ZONE_ID=$(vget "/iaas/api/zones"    | sed 's/},{/}\n{/g' | grep -F "vc-${PROJECT_NAME}" | jqid)
echo "    region_id=$REGION_ID  zone_id=$ZONE_ID"

echo "==> 3. Create the Project"
PROJECT_ID=$(vpost "/iaas/api/projects" \
  "{\"name\":\"$PROJECT_NAME\",\"zoneAssignmentConfigurations\":[{\"zoneId\":\"$ZONE_ID\",\"priority\":1,\"maxNumberInstances\":0}]}" \
  | sed 's/,/\n/g' | grep -A1 "\"name\":\"$PROJECT_NAME\"" | grep -oE '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
echo "    project_id=$PROJECT_ID"

echo "==> 4. Wait for inventory enumeration, then resolve fabric object IDs"
for i in $(seq 1 15); do
  n=$(vget "/iaas/api/fabric-images?\$top=1" | grep -oE '"totalElements":[0-9]+' | cut -d: -f2)
  [ "${n:-0}" -gt 5 ] && break; sleep 20
done
# fabric image IDs are long hashes - parse by name (do NOT scrape the first "id" you see)
fabid(){ vget "$1?\$top=300" | sed 's/},{/}\n{/g' | grep -F "\"name\":\"$2\"" | jqid; }
IMG_CENTOS=$(fabid /iaas/api/fabric-images "$IMG_CENTOS_TEMPLATE")
IMG_UBUNTU=$(fabid /iaas/api/fabric-images "$IMG_UBUNTU_TEMPLATE")
NET_ID=$(fabid /iaas/api/fabric-networks "$NETWORK_NAME")
DS_ID=$(fabid /iaas/api/fabric-vsphere-datastores "$DATASTORE_NAME")
echo "    centos=$IMG_CENTOS ubuntu=$IMG_UBUNTU net=$NET_ID ds=$DS_ID"

echo "==> 5. Create Flavor / Image / Network / Storage profiles"
vpost "/iaas/api/flavor-profiles" \
  "{\"name\":\"lab-flavors\",\"regionId\":\"$REGION_ID\",\"flavorMapping\":{\"small\":{\"cpuCount\":1,\"memoryInMB\":2048},\"medium\":{\"cpuCount\":2,\"memoryInMB\":4096}}}" >/dev/null
vpost "/iaas/api/image-profiles" \
  "{\"name\":\"lab-images\",\"regionId\":\"$REGION_ID\",\"imageMapping\":{\"centos7\":{\"id\":\"$IMG_CENTOS\"},\"ubuntu\":{\"id\":\"$IMG_UBUNTU\"}}}" >/dev/null
vpost "/iaas/api/network-profiles" \
  "{\"name\":\"lab-net\",\"regionId\":\"$REGION_ID\",\"fabricNetworkIds\":[\"$NET_ID\"]}" >/dev/null
vpost "/iaas/api/storage-profiles-vsphere" \
  "{\"name\":\"lab-storage\",\"regionId\":\"$REGION_ID\",\"defaultItem\":true,\"datastoreId\":\"$DS_ID\",\"diskType\":\"standard\",\"provisioningType\":\"thin\"}" >/dev/null

echo "==> 6. Give the network a subnet + a static IP range (so 'assignment: static' works)"
vpatch "/iaas/api/fabric-networks-vsphere/$NET_ID" \
  "{\"cidr\":\"$IP_CIDR\",\"defaultGateway\":\"$IP_GATEWAY\",\"dnsServerAddresses\":[\"$IP_DNS\"],\"domain\":\"$IP_DOMAIN\"}" >/dev/null
vpost "/iaas/api/network-ip-ranges" \
  "{\"name\":\"lab-static\",\"startIPAddress\":\"$IP_RANGE_START\",\"endIPAddress\":\"$IP_RANGE_END\",\"ipVersion\":\"IPv4\",\"fabricNetworkIds\":[\"$NET_ID\"]}" >/dev/null

echo "PROJECT_ID=$PROJECT_ID" > .project
echo "==> Done. Project id saved to scripts/.project"
