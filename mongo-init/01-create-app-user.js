db = db.getSiblingDB("eventhub");

db.createUser({
  user: "eventhub_app",
  pwd: "eventhub_secret",
  roles: [{ role: "readWrite", db: "eventhub" }],
});
