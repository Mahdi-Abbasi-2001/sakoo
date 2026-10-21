const express = require('express');
const app = express();
const db = process.env.DATABASE_URL;
const jwt = process.env.JWT_SECRET;
app.listen(process.env.PORT || 3000);
