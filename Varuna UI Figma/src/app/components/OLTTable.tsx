import React from 'react';
import { MoreHorizontal, Power, MapPin, Activity } from 'lucide-react';

export const OLTTable = () => {
  const olts = [
    { id: 1, name: 'OLT-DOWNTOWN-01', ip: '10.200.1.10', model: 'Huawei MA5800-X17', status: 'online', ports: '12/16', onus: 842 },
    { id: 2, name: 'OLT-UPTOWN-04', ip: '10.200.1.11', model: 'ZTE C600', status: 'online', ports: '14/16', onus: 915 },
    { id: 3, name: 'OLT-SUBURB-02', ip: '10.200.2.05', model: 'Nokia FX-8', status: 'warning', ports: '8/16', onus: 420 },
    { id: 4, name: 'OLT-INDUSTRIAL-01', ip: '10.200.4.12', model: 'Huawei MA5800-X7', status: 'online', ports: '4/8', onus: 210 },
    { id: 5, name: 'OLT-DOWNTOWN-02', ip: '10.200.1.12', model: 'FiberHome AN6000', status: 'offline', ports: '0/16', onus: 0 },
  ];

  return (
    <div className="bg-white rounded-2xl border border-gray-100 shadow-sm overflow-hidden">
      <div className="p-6 border-b border-gray-50 flex justify-between items-center">
        <h3 className="text-lg font-bold text-gray-900">OLT Inventory</h3>
        <button className="text-indigo-600 hover:text-indigo-700 font-medium text-sm">Add New OLT</button>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-left">
          <thead>
            <tr className="bg-gray-50/50">
              <th className="px-6 py-4 text-xs font-semibold text-gray-500 uppercase tracking-wider">Device Name</th>
              <th className="px-6 py-4 text-xs font-semibold text-gray-500 uppercase tracking-wider">IP Address</th>
              <th className="px-6 py-4 text-xs font-semibold text-gray-500 uppercase tracking-wider">Model</th>
              <th className="px-6 py-4 text-xs font-semibold text-gray-500 uppercase tracking-wider">Status</th>
              <th className="px-6 py-4 text-xs font-semibold text-gray-500 uppercase tracking-wider">PON Ports</th>
              <th className="px-6 py-4 text-xs font-semibold text-gray-500 uppercase tracking-wider">Total ONUs</th>
              <th className="px-6 py-4 text-xs font-semibold text-gray-500 uppercase tracking-wider"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50">
            {olts.map((olt) => (
              <tr key={olt.id} className="hover:bg-gray-50 transition-colors">
                <td className="px-6 py-4">
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-lg bg-gray-100 flex items-center justify-center">
                      <Activity className="w-4 h-4 text-gray-500" />
                    </div>
                    <span className="font-semibold text-gray-900">{olt.name}</span>
                  </div>
                </td>
                <td className="px-6 py-4 text-sm text-gray-600 font-mono">{olt.ip}</td>
                <td className="px-6 py-4 text-sm text-gray-600">{olt.model}</td>
                <td className="px-6 py-4">
                  <div className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${
                    olt.status === 'online' ? 'bg-green-100 text-green-700' :
                    olt.status === 'warning' ? 'bg-yellow-100 text-yellow-700' :
                    'bg-red-100 text-red-700'
                  }`}>
                    <div className={`w-1.5 h-1.5 rounded-full ${
                      olt.status === 'online' ? 'bg-green-500' :
                      olt.status === 'warning' ? 'bg-yellow-500' :
                      'bg-red-500'
                    }`} />
                    {olt.status.toUpperCase()}
                  </div>
                </td>
                <td className="px-6 py-4 text-sm text-gray-600">{olt.ports}</td>
                <td className="px-6 py-4 text-sm text-gray-600">{olt.onus}</td>
                <td className="px-6 py-4 text-right">
                  <button className="p-1 hover:bg-gray-100 rounded-lg text-gray-400">
                    <MoreHorizontal className="w-5 h-5" />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};
