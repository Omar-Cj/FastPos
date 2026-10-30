-- Run once as the MySQL root user:  sudo mysql < scripts/mysql_setup.sql
CREATE DATABASE IF NOT EXISTS fastpos CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER IF NOT EXISTS 'fastpos'@'localhost' IDENTIFIED BY 'fastpos';
CREATE USER IF NOT EXISTS 'fastpos'@'127.0.0.1' IDENTIFIED BY 'fastpos';
GRANT ALL PRIVILEGES ON fastpos.* TO 'fastpos'@'localhost';
GRANT ALL PRIVILEGES ON fastpos.* TO 'fastpos'@'127.0.0.1';
-- pytest creates/drops test_fastpos
GRANT ALL PRIVILEGES ON test_fastpos.* TO 'fastpos'@'localhost';
GRANT ALL PRIVILEGES ON test_fastpos.* TO 'fastpos'@'127.0.0.1';
FLUSH PRIVILEGES;
