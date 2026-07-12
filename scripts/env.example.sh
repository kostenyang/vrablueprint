# Copy to env.sh and fill in. NEVER commit env.sh (it is git-ignored).
# vRA (Aria Automation) appliance
export VRA_URL="https://vra.example.lab"
export VRA_USER="admin"
export VRA_PASS="CHANGE_ME"            # vIDM System Domain admin password
export VRA_DOMAIN="System Domain"

# vCenter to register as a Cloud Account
export VC_HOST="10.0.0.101"
export VC_USER="administrator@vsphere.local"
export VC_PASS="CHANGE_ME"

# Infra naming / placement (adjust to your vCenter inventory names)
export PROJECT_NAME="lab"
export DATASTORE_NAME="pcssd3"         # a datastore visible to the cluster
export NETWORK_NAME="VM Network"       # an existing port group
export IMG_CENTOS_TEMPLATE="Centos7"           # vCenter VM template name -> image "centos7"
export IMG_UBUNTU_TEMPLATE="ubuntu2004temp"    # vCenter VM template name -> image "ubuntu"

# Static IP pool handed out to deployed VMs (must be free on NETWORK_NAME's subnet)
export IP_CIDR="10.0.0.0/23"
export IP_GATEWAY="10.0.0.1"
export IP_DNS="10.0.0.200"
export IP_DOMAIN="example.lab"
export IP_RANGE_START="10.0.0.211"
export IP_RANGE_END="10.0.0.219"
