const normalize = (value) => (value || '').toString().toLowerCase()

export const classifyOnu = (onu) => {
  const status = normalize(onu?.status)
  const reason = normalize(onu?.disconnect_reason)
  const isOnline = status === 'online'

  if (isOnline) {
    return { status: 'online', label: 'ONLINE' }
  }

  if (reason.includes('dying')) {
    return { status: 'dying_gasp', label: 'DYING GASP' }
  }

  if (reason.includes('link')) {
    return { status: 'link_loss', label: 'LINK LOSS' }
  }

  return { status: status || 'offline', label: (status || 'OFFLINE').toUpperCase() }
}

export const getOnuStats = (onus = []) => {
  const normalizedOnus = Array.isArray(onus) ? onus : Object.values(onus || {})
  const stats = {
    total: 0,
    online: 0,
    offline: 0,
    dyingGasp: 0,
    linkLoss: 0,
    unknown: 0,
  }

  normalizedOnus.forEach((onu) => {
    stats.total += 1
    const { status } = classifyOnu(onu)
    if (status === 'online') {
      stats.online += 1
      return
    }

    stats.offline += 1

    if (status === 'dying_gasp') {
      stats.dyingGasp += 1
      return
    }

    if (status === 'link_loss') {
      stats.linkLoss += 1
      return
    }

    stats.unknown += 1
  })

  return stats
}

export const isZteOlt = (olt) => {
  const vendor = normalize(olt?.vendor_profile_name)
  const name = normalize(olt?.name)
  return vendor.includes('zte') || name.includes('zte')
}
