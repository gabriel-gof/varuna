"""
Serviço de Cache Redis
Redis Cache Service
"""
import json
import logging
from typing import Optional, Dict, Any, Iterable, List
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

    def get_many(self, keys: Iterable[str]) -> Dict[str, Any]:
        """
        Obtém múltiplas chaves em uma única chamada Redis.
        """
        if not self.redis_client:
            return {}

        key_list = [key for key in keys if key]
        if not key_list:
            return {}

        try:
            values = self.redis_client.mget(key_list)
            result: Dict[str, Any] = {}
            for key, value in zip(key_list, values):
                if value is None:
                    continue
                try:
                    result[key] = json.loads(value)
                except Exception:
                    logger.warning("Valor inválido no cache para chave %s", key)
            return result
        except Exception as e:
            logger.error(f"Erro ao ler múltiplas chaves do cache: {e}")
            return {}
    
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
        key = self.get_onu_status_key(olt_id, onu_id)
        return self.get(key)
    
    def set_onu_status(self, olt_id: int, onu_id: int, status_data: Dict[str, Any], ttl: int = 180) -> bool:
        """
        Define status da ONU no cache
        Sets ONU status in cache
        """
        key = self.get_onu_status_key(olt_id, onu_id)
        return self.set(key, status_data, ttl)
    
    def get_onu_power(self, olt_id: int, onu_id: int) -> Optional[Dict[str, Any]]:
        """
        Obtém potência da ONU do cache
        Gets ONU power from cache
        """
        key = self.get_onu_power_key(olt_id, onu_id)
        return self.get(key)
    
    def set_onu_power(self, olt_id: int, onu_id: int, power_data: Dict[str, Any], ttl: int = 60) -> bool:
        """
        Define potência da ONU no cache
        Sets ONU power in cache
        """
        key = self.get_onu_power_key(olt_id, onu_id)
        return self.set(key, power_data, ttl)

    def get_many_onu_status(self, olt_id: int, onu_ids: Iterable[int]) -> Dict[int, Dict[str, Any]]:
        keys = [self.get_onu_status_key(olt_id, onu_id) for onu_id in onu_ids]
        by_key = self.get_many(keys)
        result: Dict[int, Dict[str, Any]] = {}
        for onu_id in onu_ids:
            key = self.get_onu_status_key(olt_id, onu_id)
            if key in by_key:
                result[onu_id] = by_key[key]
        return result

    def get_many_onu_power(self, olt_id: int, onu_ids: Iterable[int]) -> Dict[int, Dict[str, Any]]:
        keys = [self.get_onu_power_key(olt_id, onu_id) for onu_id in onu_ids]
        by_key = self.get_many(keys)
        result: Dict[int, Dict[str, Any]] = {}
        for onu_id in onu_ids:
            key = self.get_onu_power_key(olt_id, onu_id)
            if key in by_key:
                result[onu_id] = by_key[key]
        return result

    @staticmethod
    def get_onu_status_key(olt_id: int, onu_id: int) -> str:
        return f"varuna:onu:{olt_id}:{onu_id}:status"

    @staticmethod
    def get_onu_power_key(olt_id: int, onu_id: int) -> str:
        return f"varuna:onu:{olt_id}:{onu_id}:power"
    
    def invalidate_olt_cache(self, olt_id: int):
        """
        Invalida cache de uma OLT
        Invalidates cache for an OLT
        """
        if not self.redis_client:
            return
        
        try:
            pattern = f"varuna:onu:{olt_id}:*"
            deleted = 0
            batch: List[str] = []
            for key in self.redis_client.scan_iter(match=pattern, count=200):
                batch.append(key)
                if len(batch) >= 200:
                    deleted += self.redis_client.delete(*batch)
                    batch = []
            if batch:
                deleted += self.redis_client.delete(*batch)
            if deleted:
                logger.info(f"Cache invalidado para OLT {olt_id}: {deleted} chaves")
        except Exception as e:
            logger.error(f"Erro ao invalidar cache OLT {olt_id}: {e}")


cache_service = CacheService()
