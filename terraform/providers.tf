# A API key é lida da variável de ambiente NEON_API_KEY.
provider "neon" {}

# Conecta como o usuário do serviço (não superusuário), o mesmo que roda as
# migrations. É esse usuário que cria o papel da Lambda.
provider "postgresql" {
  host            = neon_project.os.database_host
  port            = 5432
  database        = var.database_name
  username        = var.app_role
  password        = data.neon_branch_role_password.app.password
  sslmode         = "require"
  superuser       = false
  connect_timeout = 15
}
