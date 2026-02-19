# [MongoDB on Ubuntu](https://www.digitalocean.com/community/tutorials/how-to-install-mongodb-on-ubuntu-20-04)

- [Official Docs](https://www.mongodb.com/docs/manual/administration/install-community/?operating-system=linux&linux-distribution=ubuntu&linux-package=default&search-linux=with-search-linux#install-mongodb-community-edition-18)
- [Mongodb Compass](https://www.mongodb.com/try/download/compass)

# Check used port

```
# get config file
ps aux | grep mongod

# extract port from config file
cat /etc/mongod.conf | grep port

# check port directly
sudo ss -tulnp | grep mongod

#

```


# Connecting from Host Machine
```
mongosh --port 27017
mongosh 'mongodb://admin:password123@localhost:27017'

mongosh 'mongodb://mongodb_exporter:mongodb_exporter@pgpractice:27017'
mongosh --host pgpractice -u admin -p $PGPWD

# create admin user
db.createUser({
  user: "admin",
  pwd: "password123",
  roles: [
    {
      role: "root",
      db: "admin"
    }
  ]
})

# change password if required
db.changeUserPassword("admin", "password123")
```

### Check port connectivity of remote server
```
nc -zv <remote_server> 27017

telnet <hostname_or_ip> 27017

psql -h <hostname_or_ip> -p 27017 -U postgres -d postgres -c "SELECT 1;"

```
