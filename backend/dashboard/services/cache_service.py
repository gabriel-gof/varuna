"""
Serviço de Cache Redis
Redis Cache Service
"""
import json
import logging
from typing import Optional, Dict, Any
import redis
from django.conf import settings

logger = logging.getLogger(__name__)


class CacheService:
    """
    Serviço para gerenciar cache em Redis
    Service to manage Redis caching
    """
    
    def __init__(self):
        self.redis_client = None
        self._connect()
    
    def _connect(self):
        """
        Conecta ao Redis
        Connects to Redis
        """
        try:
            redis_url = getattr(settings, 'REDIS_URL', 'redis://localhost:6379/0')
            self.redis_client = redis.from_url(redis_url, decode_responses=True)
            logger.info(f"Conectado ao Redis: {redis_url}")
        except Exception as e:
            logger.error(f"Falha ao conectar ao Redis: {e}")
            self.redis_client = None
    
    def get(self, key: str) -> Optional[Any]:
        """
        Obtém valor do cache
        Gets value from cache
        """
        if not self.redis_client:
            return None
        
        try:
            value = self.redis_client.get(key)
            if value:
                return json.loads(value)
            return None
        except Exception as e:
            logger.error(f"Erro ao ler do cache ({key}): {e}")
            return None
    
    def set(self, key: str, value: Any, ttl: int = 300) -> bool:
        """
        Define valor no cache
        Sets value in cache
        """
        if not self.redis_client:
            return False
        
        try:
            json_value = json.dumps(value)
            self.redis_client.setex(key, ttl, json_value)
            return True
        except Exception as e:
            logger.error(f"Erro ao escrever no cache ({key}): {e}")
            return False
    
    def delete(self, key: str) -> bool:
        """
        Remove valor do cache
        Removes value from cache
        """
        if not self.redis_client:
            return False
        
        try:
            self.redis_client.delete(key)
            return True
        except Exception as e:
            logger.error(f"Erro ao deletar do cache ({key}): {e}")
            return False
    
    def get_onu_status(self, olt_id: int, onu_id: int) -> Optional[Dict[str, Any]]:
        """
        Obtém status da ONU do cache
        Gets ONU status from cache
        """
        key = f"varuna:onu:{olt_id}:{onu_id}:status"
        ttl = getattr(settings, 'STATUS_CACHE_TTL', 180)
        return self.get(key)
    
    def set_onu_status(self, olt_id: int, onu_id: int, status_data: Dict[str, Any], ttl: int = 180) -> bool:
        """
        Define status da ONU no cache
        Sets ONU status in cache
        """
        key = f"varuna:onu:{olt_id}:{onu_id}:status"
        return self.set(key, status_data, ttl)
    
    def get_onu_power(self, olt_id: int, onu_id: int) -> Optional[Dict[str, Any]]:
        """
        Obtém potência da ONU do cache
        Gets ONU power from cache
        """
        key = f"varuna:onu:{olt_id}:{onu_id}:power"
        return self.get(key)
    
    def set_onu_power(self, olt_id: int, onu_id: int, power_data: Dict[str, Any], ttl: int = 60) -> bool:
        """
        Define potência da ONU no cache
        Sets ONU power in cache
        """
        key = f"varuna:onu:{olt_id}:{onu_id}:power"
        return self.set(key, power_data, ttl)
    
    def invalidate_olt_cache(self, olt_id: int):
        """
        Invalida cache de uma OLT
        Invalidates cache for an OLT
        """
        if not self.redis_client:
            return
        
        try:
            pattern = f"varuna:onu:{olt_id}:*"
            keys = self.redis_client.keys(pattern)
            if keys:
                self.redis_client.delete(*keys)
                logger.info(f"Cache invalidado para OLT {olt_id}: {len(keys)} chaves")
        except Exception as e:
            logger.error(f"Erro ao invalidar cache OLT {olt_id}: {e}")


cache_service = CacheService()
