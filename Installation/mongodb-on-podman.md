# Running MongoDB Locally with Podman

## Step 1: Install Podman
```
podman --version
```

## Step 2: Pull MongoDB Image
```
podman pull docker.io/mongodb/mongodb-community-server:latest
```

## Step 3: Verify Image
```
podman images
```

## Step 4: Run MongoDB Container with Volume (Data Persists)
```
# create directory for peristence
mkdir -p /hyperactive/podman_mongodb_disk

# find podman internal uid, and assign correct permission for same uid on hypervisor host
podman run --rm docker.io/mongodb/mongodb-community-server:latest id
  uid=101(mongodb) gid=65534(nogroup) groups=65534(nogroup),101(mongodb)

podman unshare chown -R 101:101 /hyperactive/podman_mongodb_disk

# Start mongodb container
podman run -d \
  --name mongodb \
  -p 27017:27017 \
  -v /hyperactive/podman_mongodb_disk:/data/db:Z \
  docker.io/mongodb/mongodb-community-server:latest


# connect to mongodb
mongosh "mongodb://admin:StrongPassword123@localhost:27017"
mongosh "mongodb://localhost:27017"

# IF required, remove the container
podman rm -f mongodb

# check logs
podman logs -f mongodb

```

## Step 5: Install MongoSh
https://www.mongodb.com/docs/mongodb-shell/install/?operating-system=linux&linux-distribution=ubuntu&ubuntu-version=noble

## Step 5: Connect to MongoDB
```
# Connect using mongosh inside container
podman exec -it mongodb mongosh
podman exec -it mongodb mongosh -u admin -p password123

# Connecting from Host Machine
mongosh --port 27017
mongosh "mongodb://admin:password123@localhost:27017"
```


