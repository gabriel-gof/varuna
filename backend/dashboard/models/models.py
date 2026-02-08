from django.db import models
from django.contrib.auth.models import User


class VendorProfile(models.Model):
    """
    Define modelos de OIDs e capacidades para um fabricante/modelo
    Defines OID templates and capabilities for a vendor/model
    """
    
    VENDOR_ZTE = 'zte'
    VENDOR_HUAWEI = 'huawei'
    VENDOR_FIBERHOME = 'fiberhome'
    VENDOR_DATACOM = 'datacom'
    
    VENDOR_CHOICES = [
        (VENDOR_ZTE, 'ZTE'),
        (VENDOR_HUAWEI, 'Huawei'),
        (VENDOR_FIBERHOME, 'FiberHome'),
        (VENDOR_DATACOM, 'Datacom'),
    ]
    
    vendor = models.CharField(max_length=50, choices=VENDOR_CHOICES, verbose_name='Fabricante')
    model_name = models.CharField(max_length=100, verbose_name='Nome do Modelo')
    description = models.TextField(blank=True, verbose_name='Descrição')
    
    oid_templates = models.JSONField(default=dict, verbose_name='Templates de OID')
    
    supports_onu_discovery = models.BooleanField(default=True, verbose_name='Suporta Descoberta de ONU')
    supports_onu_status = models.BooleanField(default=True, verbose_name='Suporta Status de ONU')
    supports_power_monitoring = models.BooleanField(default=True, verbose_name='Suporta Monitoramento de Potência')
    supports_disconnect_reason = models.BooleanField(default=True, verbose_name='Suporta Motivo de Desconexão')
    
    default_thresholds = models.JSONField(default=dict, verbose_name='Limites Padrão')
    
    is_active = models.BooleanField(default=True, verbose_name='Ativo')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Criado em')
    
    def __str__(self):
        return f"{self.get_vendor_display()} {self.model_name}"
    
    class Meta:
        verbose_name = 'Perfil de Fabricante'
        verbose_name_plural = 'Perfis de Fabricante'
        unique_together = ['vendor', 'model_name']
        indexes = [
            models.Index(fields=['vendor']),
        ]


class OLT(models.Model):
    """
    Dispositivo OLT físico
    Physical OLT device
    """
    
    PROTOCOL_SNMP = 'snmp'
    PROTOCOL_CHOICES = [
        (PROTOCOL_SNMP, 'SNMP'),
    ]
    
    name = models.CharField(max_length=100, unique=True, verbose_name='Nome')
    vendor_profile = models.ForeignKey(VendorProfile, on_delete=models.CASCADE, related_name='olts', verbose_name='Perfil de Fabricante')
    protocol = models.CharField(max_length=20, choices=PROTOCOL_CHOICES, default=PROTOCOL_SNMP, verbose_name='Protocolo')
    
    ip_address = models.GenericIPAddressField(verbose_name='Endereço IP')
    snmp_port = models.IntegerField(default=161, verbose_name='Porta SNMP')
    snmp_community = models.CharField(max_length=100, verbose_name='Comunidade SNMP')
    snmp_version = models.CharField(max_length=10, default='v2c', choices=[('v2c', 'v2c'), ('v3', 'v3')], verbose_name='Versão SNMP')
    
    discovery_enabled = models.BooleanField(default=True, verbose_name='Descoberta Ativada')
    discovery_interval_minutes = models.IntegerField(
        default=240,
        help_text='Com que frequência descobrir a estrutura da OLT (slots, PONs, ONUs)',
        verbose_name='Intervalo de Descoberta (minutos)'
    )
    last_discovery_at = models.DateTimeField(null=True, blank=True, verbose_name='Última Descoberta')
    next_discovery_at = models.DateTimeField(null=True, blank=True, verbose_name='Próxima Descoberta')
    discovery_healthy = models.BooleanField(default=True, verbose_name='Descoberta Saudável')
    
    polling_enabled = models.BooleanField(default=True, verbose_name='Polling Ativado')
    polling_interval_seconds = models.IntegerField(
        default=300,
        help_text='Com que frequência verificar o status online/offline das ONUs',
        verbose_name='Intervalo de Polling (segundos)'
    )
    last_poll_at = models.DateTimeField(null=True, blank=True, verbose_name='Último Polling')
    next_poll_at = models.DateTimeField(null=True, blank=True, verbose_name='Próximo Polling')
    
    is_active = models.BooleanField(default=True, verbose_name='Ativo')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Criado em')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Atualizado em')
    
    def __str__(self):
        return f"{self.name} ({self.vendor_profile.get_vendor_display()} {self.vendor_profile.model_name})"
    
    class Meta:
        verbose_name = 'OLT'
        verbose_name_plural = 'OLTs'
        indexes = [
            models.Index(fields=['vendor_profile']),
        ]


class OLTSlot(models.Model):
    """
    Slot físico de uma OLT
    Physical slot on an OLT
    """

    olt = models.ForeignKey(OLT, on_delete=models.CASCADE, related_name='slots', verbose_name='OLT')
    slot_id = models.IntegerField(verbose_name='Slot ID')
    rack_id = models.IntegerField(null=True, blank=True, verbose_name='Rack ID')
    shelf_id = models.IntegerField(null=True, blank=True, verbose_name='Shelf ID')
    slot_key = models.CharField(max_length=100, verbose_name='Chave do Slot')
    name = models.CharField(max_length=200, blank=True, verbose_name='Nome')

    is_active = models.BooleanField(default=True, verbose_name='Ativo')
    last_discovered_at = models.DateTimeField(auto_now=True, verbose_name='Última Descoberta')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Criado em')

    def __str__(self):
        return f"Slot {self.slot_id} @ {self.olt.name}"

    class Meta:
        verbose_name = 'Slot'
        verbose_name_plural = 'Slots'
        unique_together = ['olt', 'slot_key']
        indexes = [
            models.Index(fields=['olt', 'slot_id'], name='dashboard_o_olt_id_1fd7ed_idx'),
            models.Index(fields=['olt', 'slot_key'], name='dashboard_o_olt_id_4e23d0_idx'),
        ]


class OLTPON(models.Model):
    """
    Porta PON de uma OLT
    PON port on an OLT
    """

    olt = models.ForeignKey(OLT, on_delete=models.CASCADE, related_name='pons', verbose_name='OLT')
    slot = models.ForeignKey(OLTSlot, on_delete=models.CASCADE, related_name='pons', verbose_name='Slot')
    pon_id = models.IntegerField(verbose_name='PON ID')
    pon_index = models.BigIntegerField(null=True, blank=True, verbose_name='Índice PON SNMP')
    rack_id = models.IntegerField(null=True, blank=True, verbose_name='Rack ID')
    shelf_id = models.IntegerField(null=True, blank=True, verbose_name='Shelf ID')
    port_id = models.IntegerField(null=True, blank=True, verbose_name='Port ID')
    pon_key = models.CharField(max_length=120, verbose_name='Chave do PON')
    name = models.CharField(max_length=200, blank=True, verbose_name='Nome')

    is_active = models.BooleanField(default=True, verbose_name='Ativo')
    last_discovered_at = models.DateTimeField(auto_now=True, verbose_name='Última Descoberta')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Criado em')

    def __str__(self):
        return f"PON {self.pon_id} @ {self.olt.name}"

    class Meta:
        verbose_name = 'PON'
        verbose_name_plural = 'PONs'
        unique_together = ['slot', 'pon_id']
        indexes = [
            models.Index(fields=['olt', 'pon_id'], name='dashboard_o_olt_id_76b9d5_idx'),
            models.Index(fields=['slot', 'pon_id'], name='dashboard_o_slot_i_2cb314_idx'),
        ]


class ONU(models.Model):
    """
    ONU descoberta em uma OLT
    Discovered ONU on an OLT
    """
    
    STATUS_ONLINE = 'online'
    STATUS_OFFLINE = 'offline'
    STATUS_UNKNOWN = 'unknown'
    
    STATUS_CHOICES = [
        (STATUS_ONLINE, 'Online'),
        (STATUS_OFFLINE, 'Offline'),
        (STATUS_UNKNOWN, 'Desconhecido'),
    ]
    
    olt = models.ForeignKey(OLT, on_delete=models.CASCADE, related_name='onus', verbose_name='OLT')
    slot_ref = models.ForeignKey(
        OLTSlot,
        on_delete=models.SET_NULL,
        related_name='onus',
        null=True,
        blank=True,
        verbose_name='Slot'
    )
    pon_ref = models.ForeignKey(
        OLTPON,
        on_delete=models.SET_NULL,
        related_name='onus',
        null=True,
        blank=True,
        verbose_name='PON'
    )
    
    slot_id = models.IntegerField(verbose_name='Slot ID')
    pon_id = models.IntegerField(verbose_name='PON ID')
    onu_id = models.IntegerField(verbose_name='ONU ID')
    
    snmp_index = models.CharField(max_length=200, unique=True, verbose_name='Índice SNMP')
    name = models.CharField(max_length=200, blank=True, verbose_name='Nome')
    serial = models.CharField(max_length=100, blank=True, verbose_name='Número de Série')
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_UNKNOWN, verbose_name='Status')
    
    last_discovered_at = models.DateTimeField(auto_now=True, verbose_name='Última Descoberta')
    
    def __str__(self):
        return f"{self.name or self.serial or self.onu_id} @ {self.olt.name}"
    
    class Meta:
        verbose_name = 'ONU'
        verbose_name_plural = 'ONUs'
        unique_together = ['olt', 'slot_id', 'pon_id', 'onu_id']
        indexes = [
            models.Index(fields=['olt', 'slot_id', 'pon_id']),
            models.Index(fields=['snmp_index']),
        ]


class ONULog(models.Model):
    """
    Registra eventos de desconexão de ONUs
    Tracks ONU offline events
    """
    
    REASON_LINK_LOSS = 'link_loss'
    REASON_DYING_GASP = 'dying_gasp'
    REASON_UNKNOWN = 'unknown'
    
    REASON_CHOICES = [
        (REASON_LINK_LOSS, 'Rompimento'),
        (REASON_DYING_GASP, 'Sem Energia'),
        (REASON_UNKNOWN, 'Desconhecido'),
    ]
    
    onu = models.ForeignKey(ONU, on_delete=models.CASCADE, related_name='logs', verbose_name='ONU')
    
    offline_since = models.DateTimeField(verbose_name='Offline Desde')
    offline_until = models.DateTimeField(null=True, blank=True, verbose_name='Offline Até')
    disconnect_reason = models.CharField(
        max_length=20,
        choices=REASON_CHOICES,
        default=REASON_UNKNOWN,
        verbose_name='Motivo da Desconexão'
    )
    
    def __str__(self):
        return f"{self.onu.name or self.onu.serial} offline em {self.offline_since}"
    
    class Meta:
        verbose_name = 'Log de ONU'
        verbose_name_plural = 'Logs de ONU'
        indexes = [
            models.Index(fields=['onu', '-offline_since']),
        ]


class UserProfile(models.Model):
    """
    Perfil de usuário extendido para informações adicionais
    Extended user profile for additional user information
    """
    
    ROLE_ADMIN = 'admin'
    ROLE_OPERATOR = 'operator'
    ROLE_VIEWER = 'viewer'
    
    ROLE_CHOICES = [
        (ROLE_ADMIN, 'Administrador'),
        (ROLE_OPERATOR, 'Operador'),
        (ROLE_VIEWER, 'Leitor'),
    ]
    
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile', verbose_name='Usuário')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_VIEWER, verbose_name='Função')
    last_login_ip = models.GenericIPAddressField(null=True, blank=True, verbose_name='Último IP de Login')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Criado em')
    
    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"
    
    def can_modify_settings(self):
        return self.role in [self.ROLE_ADMIN, self.ROLE_OPERATOR]
    
    class Meta:
        verbose_name = 'Perfil de Usuário'
        verbose_name_plural = 'Perfis de Usuário'
