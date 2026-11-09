# Banco próprio do OS Service: um projeto Neon só dele, com credenciais que nenhum
# outro serviço recebe.
resource "neon_project" "os" {
  name                      = var.project_name
  org_id                    = var.org_id
  region_id                 = var.region
  pg_version                = var.pg_version
  history_retention_seconds = var.history_retention_seconds

  branch {
    name          = "main"
    database_name = var.database_name
    role_name     = var.app_role
  }
}

# Usuário da Lambda de autenticação, criado pelo próprio usuário do serviço e
# não pela API do Neon. Papel criado pela API entra no grupo `neon_superuser`,
# que lê todas as tabelas do banco (pg_read_all_data): a Lambda enxergaria
# veículos e ordens de serviço, não só a view de quatro colunas. Criado pelo
# usuário do serviço, o papel não herda nada, e o serviço pode administrá-lo.
#
# As permissões (só a view auth.clients) são dadas pelas migrations 002 e 006,
# que rodam depois deste apply.
resource "random_password" "auth_reader" {
  length  = 32
  special = false # vai dentro da URL de conexão
}

resource "postgresql_role" "auth_reader" {
  name             = var.auth_role
  login            = true
  password         = random_password.auth_reader.result
  search_path      = ["auth"]
  connection_limit = 10
}

data "neon_branch_role_password" "app" {
  project_id = neon_project.os.id
  branch_id  = neon_project.os.default_branch_id
  role_name  = var.app_role
}
