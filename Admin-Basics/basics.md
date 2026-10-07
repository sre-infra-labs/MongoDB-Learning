# MongoDB Document

- JSON:
  - _id field - unique & mandatory
  - single field, array, sub document, array of sub documents
  - Dynamic


# JSON Structure in MongoDB

```
{
  _id: 1,
  name: "John",
  age: 30,
  phone: ["555-123-4567", "555-789-0123"]
  address: {
              city: "Anytown",
              state: "CA"
            },
  Experience: [{company: "ABC",years: 5}, {company: "DEF",years: 3}]
}

-- To access company of 2nd second experience
db.collection.find({"Experience.1.company": 1})
or
db.collection.find({"_id": 1}, {"Experience.1.company": 1})

```

# 

