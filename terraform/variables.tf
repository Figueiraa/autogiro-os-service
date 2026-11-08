variable "org_id" {
  description = "Identificador da organização no Neon (formato org-xxxx-xxxx-00000000)."
  type        = string
}

variable "project_name" {
  description = "Nome do projeto no Neon. Cada serviço tem o seu."
  type        = string
  default     = "autogiro-os"
}

variable "region" {
  description = "Região do Neon (formato aws-<região>)."
  type        = string
  default     = "aws-us-east-2"
}

variable "pg_version" {
  description = "Versão do PostgreSQL."
  type        = number
  default     = 17
}

variable "database_name" {
  description = "Nome do banco do OS Service."
  type        = string
  default     = "autogiro_os"
}

variable "app_role" {
  description = "Usuário da aplicação (dono das tabelas)."
  type        = string
  default     = "os_service"
}

variable "auth_role" {
  description = "Usuário somente leitura da Lambda autogiro-auth (enxerga só a view auth.clients)."
  type        = string
  default     = "auth_reader"
}

variable "history_retention_seconds" {
  description = "Janela de point-in-time recovery. O plano gratuito do Neon aceita no máximo 21600 (6 h)."
  type        = number
  default     = 21600

  validation {
    condition     = var.history_retention_seconds <= 21600
    error_message = "O plano gratuito do Neon limita a retenção a 21600 segundos (6 horas)."
  }
}
