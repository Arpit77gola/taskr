import bcrypt

password = "123456"   # tu jo password chaahe rakh sakta hai
hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())

print(hashed.decode('utf-8'))