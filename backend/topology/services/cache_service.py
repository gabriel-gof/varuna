"""
Serviço de Cache Redis
Redis Cache Service
"""
import hashlib
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
    
    def set_many(self, items: Dict[str, Any], ttl: int = 300) -> bool:
        if not self.redis_client or not items:
            return False
        try:
            pipe = self.redis_client.pipeline()
            for key, value in items.items():
                pipe.setex(key, ttl, json.dumps(value))
            pipe.execute()
            return True
        except Exception as e:
            logger.error("Erro ao escrever batch no cache (%s keys): %s", len(items), e)
            return False

    def set_many_onu_status(self, olt_id: int, entries: Dict[int, Dict[str, Any]], ttl: int = 180) -> bool:
        items = {
            self.get_onu_status_key(olt_id, onu_id): data
            for onu_id, data in entries.items()
        }
        return self.set_many(items, ttl=ttl)

    def set_many_onu_power(self, olt_id: int, entries: Dict[int, Dict[str, Any]], ttl: int = 60) -> bool:
        items = {
            self.get_onu_power_key(olt_id, onu_id): data
            for onu_id, data in entries.items()
        }
        return self.set_many(items, ttl=ttl)

    @staticmethod
    def _hash_signature(signature: str) -> str:
        normalized = str(signature or '')
        return hashlib.sha1(normalized.encode('utf-8')).hexdigest()[:16]

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

    @staticmethod
    def get_topology_structure_key(olt_id: int) -> str:
        return f"varuna:topology:structure:{olt_id}"

    def get_topology_structure(self, olt_id: int) -> Optional[Dict[str, Any]]:
        return self.get(self.get_topology_structure_key(olt_id))

    def get_many_topology_structures(self, olt_ids: Iterable[int]) -> Dict[int, Dict[str, Any]]:
        normalized_ids = []
        for raw in olt_ids:
            try:
                normalized_ids.append(int(raw))
            except (TypeError, ValueError):
                continue

        keys = [self.get_topology_structure_key(olt_id) for olt_id in normalized_ids]
        by_key = self.get_many(keys)
        result: Dict[int, Dict[str, Any]] = {}
        for olt_id in normalized_ids:
            key = self.get_topology_structure_key(olt_id)
            if key in by_key:
                result[olt_id] = by_key[key]
        return result

    def set_topology_structure(self, olt_id: int, structure: Dict[str, Any], ttl: Optional[int] = None) -> bool:
        effective_ttl = int(ttl or getattr(settings, 'TOPOLOGY_STRUCTURE_CACHE_TTL', 43200) or 43200)
        return self.set(self.get_topology_structure_key(olt_id), structure, ttl=effective_ttl)

    @classmethod
    def get_api_olts_key(cls, *, include_topology: bool, query_signature: str = '') -> str:
        mode = 'topology' if include_topology else 'base'
        return f"varuna:api:olts:{mode}:{cls._hash_signature(query_signature)}"

    @staticmethod
    def get_api_olt_topology_key(olt_id: int) -> str:
        return f"varuna:api:olt:{olt_id}:topology"

    def _delete_by_patterns(self, patterns: Iterable[str]) -> int:
        if not self.redis_client:
            return 0

        deleted = 0
        try:
            batch: List[str] = []
            for pattern in patterns:
                for key in self.redis_client.scan_iter(match=pattern, count=200):
                    batch.append(key)
                    if len(batch) >= 200:
                        deleted += self.redis_client.delete(*batch)
                        batch = []
            if batch:
                deleted += self.redis_client.delete(*batch)
        except Exception as e:
            logger.error("Erro ao deletar cache por padrão (%s): %s", ','.join(patterns), e)
        return deleted

    def invalidate_topology_api_cache(self, olt_id: int | None = None):
        if not self.redis_client:
            return

        patterns = ['varuna:api:olts:*']
        if olt_id is None:
            patterns.append('varuna:api:olt:*')
        else:
            patterns.append(f'varuna:api:olt:{olt_id}:*')

        deleted = self._delete_by_patterns(patterns)
        if deleted:
            logger.info("API topology cache invalidated (olt=%s, keys=%s)", olt_id, deleted)

    def invalidate_topology_structure_cache(self, olt_id: int | None = None):
        if not self.redis_client:
            return

        patterns = ['varuna:topology:structure:*'] if olt_id is None else [self.get_topology_structure_key(int(olt_id))]
        deleted = self._delete_by_patterns(patterns)
        if deleted:
            logger.info("Topology structure cache invalidated (olt=%s, keys=%s)", olt_id, deleted)
    
    def invalidate_olt_cache(self, olt_id: int):
        """
        Invalida cache de uma OLT
        Invalidates cache for an OLT
        """
        if not self.redis_client:
            return

        patterns = [
            f"varuna:onu:{olt_id}:*",
            "varuna:api:olts:*",
            f"varuna:api:olt:{olt_id}:*",
            self.get_topology_structure_key(olt_id),
        ]
        deleted = self._delete_by_patterns(patterns)
        if deleted:
            logger.info("Cache invalidado para OLT %s: %s chaves", olt_id, deleted)


cache_service = CacheService()
