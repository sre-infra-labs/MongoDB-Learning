# MongoDB Exporter
- [MongoDB Exporter from Percona](https://github.com/percona/mongodb_exporter)

[Blog post](https://www.digitalocean.com/community/tutorials/how-to-monitor-mongodb-with-grafana-and-prometheus-on-ubuntu-20-04)

## *Step 1*: Setting up MongoDB Exporter
```
cd ~/Downloads

# download binary
wget https://github.com/percona/mongodb_exporter/releases/download/v0.47.2/mongodb_exporter-0.47.2.linux-amd64.tar.gz

# unzip tarball
tar xvfz mongodb_exporter-*.linux-amd64.tar.gz

# copy binary
sudo cp ./mongodb_exporter-0.47.2.linux-amd64/mongodb_exporter /usr/local/bin/

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
- job_name: mongodb_exporter
  static_configs:
    - targets:
      - "pgpractice:9216"
```

# Grafana Dashboards for MongoDB
- [percona/grafana-dashboards](https://github.com/percona/grafana-dashboards/tree/main/dashboards/MongoDB)
- [MongoDB Overview](https://grafana.com/grafana/dashboards/20192-mongodb-overview/) - working
- [Mongodb Dashboard](https://grafana.com/grafana/dashboards/20867-mongodb-dashboard/) - working | only couple of panels not working

- [MongoDB](https://grafana.com/grafana/dashboards/14997-mongodb/) - partial - os working | mongodb not working
or
- [MongoDB](https://grafana.com/grafana/dashboards/2583-mongodb/) - partial - os working | mongodb not working

- [MongoDB Overview](https://grafana.com/grafana/dashboards/7353-mongodb-overview/) - not working
- [MongoDB Instance Summary](https://grafana.com/grafana/dashboards/14547-mongodb-instance-summary/) - not working
- 
