# MongoDB Exporter
- [MongoDB Exporter from Percona](https://github.com/percona/mongodb_exporter)
- [MongoDB Exporter from dcu](https://github.com/dcu/mongodb_exporter?tab=readme-ov-file)

[Blog post](https://www.digitalocean.com/community/tutorials/how-to-monitor-mongodb-with-grafana-and-prometheus-on-ubuntu-20-04)

## *Step 1*: Setting up MongoDB Exporter
```
cd ~/Downloads

# download binary
wget https://github.com/percona/mongodb_exporter/releases/download/v0.47.2/mongodb_exporter-0.47.2.linux-amd64.tar.gz
or
wget https://github.com/dcu/mongodb_exporter/releases/download/v1.0.0/mongodb_exporter-linux-amd64

# unzip tarball
tar xvfz mongodb_exporter-*.linux-amd64.tar.gz

# copy binary
sudo cp ./mongodb_exporter-0.47.2.linux-amd64/mongodb_exporter /usr/local/bin/
or
sudo cp ./mongodb_exporter-linux-amd64 /usr/local/bin/mongodb_exporter

sudo ls -l /usr/local/bin/mongo*

# create user for mongodb_exporter
mongosh --host pgpractice -u admin
  use admin
  db.createUser({user: "mongodb_exporter",pwd: "mongodb_exporter",roles: [{ role: "clusterMonitor", db: "admin" },{ role: "read", db: "local" }]})

# set MongoDB URI environment variable
export MONGODB_URI=mongodb://mongodb_exporter:mongodb_exporter@localhost:27017
env | grep mongodb

# Test connectivity (https://github.com/percona/mongodb_exporter?tab=readme-ov-file#mongodb-authentication)
mongosh 'mongodb://mongodb_exporter:mongodb_exporter@pgpractice:27017'

mongodb_exporter --mongodb.uri=mongodb://127.0.0.1:27017
mongodb_exporter --mongodb.uri='mongodb://mongodb_exporter:mongodb_exporter@localhost:27017'
or
mongodb_exporter -mongodb.uri 'mongodb://mongodb_exporter:mongodb_exporter@localhost:27017'

# Test if things worked
curl http://localhost:9216/metrics

# add firewall exception if needed
sudo ufw allow 9216/tcp

```


## *Step 2*: Setup Environment File

```
# create file
sudo nano /etc/default/mongodb_exporter

  MONGODB_URI='mongodb://mongodb_exporter:mongodb_exporter@localhost:27017'

# set permissions
sudo chmod 600 /etc/default/mongodb_exporter
sudo chown root:root /etc/default/mongodb_exporter

```


## *Step 3*: Create a systemd service

create systemd service file.
```
sudo nano /etc/systemd/system/mongodb_exporter.service
```

Add following content -
```
[Unit]
Description=MongoDB Exporter
Documentation=https://github.com/percona/mongodb_exporter
After=network-online.target mongod.service # Ensure network and MongoDB are up

[Service]
Type=simple
Restart=always
EnvironmentFile=/etc/default/mongodb_exporter
ExecStart=/usr/local/bin/mongodb_exporter --mongodb.uri=${MONGODB_URI} --collect-all

[Install]
WantedBy=multi-user.target
```

Reload systemd and Enable/Start the service
```
sudo systemctl daemon-reload
sudo systemctl enable mongodb_exporter.service
sudo systemctl start mongodb_exporter.service
sudo journalctl -u mongodb_exporter.service -f

sudo systemctl status mongodb_exporter.service

    saanvi@pgpractice:/lib/systemd/system$ sudo systemctl status mongodb_exporter.service 
    ● mongodb_exporter.service - MongoDB Exporter
        Loaded: loaded (/etc/systemd/system/mongodb_exporter.service; enabled; vendor preset: enabled)
        Active: active (running) since Thu 2026-02-19 16:58:02 IST; 37s ago
          Docs: https://github.com/percona/mongodb_exporter
      Main PID: 3190 (mongodb_exporte)
          Tasks: 7 (limit: 18985)
        Memory: 6.8M
            CPU: 29ms
        CGroup: /system.slice/mongodb_exporter.service
                └─3190 /usr/local/bin/mongodb_exporter --mongodb.uri=mongodb://mongodb_exporter:mongodb_exporter@localhost:27017

    Feb 19 16:58:02 pgpractice systemd[1]: Started MongoDB Exporter.


# Test Metrics
sudo curl http://localhost:9216/metrics
```

## *Step 2*: Scraping MongoDB Exporter using Prometheus

Edit /etc/prometheus/prometheus.yml

```
- job_name: mongodb
  static_configs:
    - targets:
      - "pgpractice:9216"
```

# Grafana Dashboards for MongoDB
- [percona/grafana-dashboards](https://github.com/percona/grafana-dashboards/tree/main/dashboards/MongoDB)

```
python misc/convert-dash-from-PMM.py dashboards/MongoDB/MongoDB_Instances_Overview.json

# if fails due to python version
2to3 -w misc/convert-dash-from-PMM.py -o . -n

python convert-dash-from-PMM.py dashboards/MongoDB/MongoDB_Backup_Details.json
python convert-dash-from-PMM.py dashboards/MongoDB/MongoDB_Cluster_Summary.json
python convert-dash-from-PMM.py dashboards/MongoDB/MongoDB_Collections_Overview.json
python convert-dash-from-PMM.py dashboards/MongoDB/MongoDB_InMemory_Details.json
python convert-dash-from-PMM.py dashboards/MongoDB/MongoDB_Instances_Compare.json
python convert-dash-from-PMM.py dashboards/MongoDB/MongoDB_Instances_Overview.json
python convert-dash-from-PMM.py dashboards/MongoDB/MongoDB_Instance_Summary.json
python convert-dash-from-PMM.py dashboards/MongoDB/MongoDB_MMAPv1_Details.json
python convert-dash-from-PMM.py dashboards/MongoDB/MongoDB_Oplog_Details.json
python convert-dash-from-PMM.py dashboards/MongoDB/MongoDB_ReplSet_Summary.json
python convert-dash-from-PMM.py dashboards/MongoDB/MongoDB_Router_Summary.json
python convert-dash-from-PMM.py dashboards/MongoDB/MongoDB_WiredTiger_Details.json
```
