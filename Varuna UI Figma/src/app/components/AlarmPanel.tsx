import React from 'react';
import { AlertTriangle, Clock, MapPin } from 'lucide-react';

export const AlarmPanel = () => {
  const alarms = [
    { id: 1, severity: 'critical', message: 'OLT Port Down: OLT-DOWNTOWN-01 PON 0/1/4', time: '2 mins ago', location: 'Rack A-12' },
    { id: 2, severity: 'warning', message: 'High Rx Power: GPON00B2 (-27.1 dBm)', time: '15 mins ago', location: 'Branch Sector 4' },
    { id: 3, severity: 'major', message: 'Dying Gasp: GPON00D4 (Loss of Power)', time: '42 mins ago', location: 'Customer Premise' },
    { id: 4, severity: 'info', message: 'Configuration backup successful', time: '1 hour ago', location: 'System' },
  ];

  return (
    <div className="bg-white rounded-2xl border border-gray-100 shadow-sm h-full flex flex-col">
      <div className="p-6 border-b border-gray-50 flex justify-between items-center">
        <h3 className="text-lg font-bold text-gray-900">Recent Alarms</h3>
        <button className="text-gray-400 hover:text-gray-600 text-sm">Clear All</button>
      </div>
      <div className="flex-1 overflow-y-auto p-2 space-y-2">
        {alarms.map((alarm) => (
          <div 
            key={alarm.id} 
            className={`p-4 rounded-xl border flex gap-4 transition-all hover:translate-x-1 ${
              alarm.severity === 'critical' ? 'bg-red-50 border-red-100' :
              alarm.severity === 'major' ? 'bg-orange-50 border-orange-100' :
              alarm.severity === 'warning' ? 'bg-amber-50 border-amber-100' :
              'bg-blue-50 border-blue-100'
            }`}
          >
            <div className={`p-2 rounded-lg h-fit ${
              alarm.severity === 'critical' ? 'bg-red-100 text-red-600' :
              alarm.severity === 'major' ? 'bg-orange-100 text-orange-600' :
              alarm.severity === 'warning' ? 'bg-amber-100 text-amber-600' :
              'bg-blue-100 text-blue-600'
            }`}>
              <AlertTriangle className="w-5 h-5" />
            </div>
            <div className="flex-1">
              <p className={`text-sm font-bold ${
                alarm.severity === 'critical' ? 'text-red-900' :
                alarm.severity === 'major' ? 'text-orange-900' :
                alarm.severity === 'warning' ? 'text-amber-900' :
                'text-blue-900'
              }`}>
                {alarm.message}
              </p>
              <div className="flex items-center gap-4 mt-2">
                <div className="flex items-center gap-1 text-[10px] font-medium text-gray-500 uppercase tracking-wider">
                  <Clock className="w-3 h-3" />
                  {alarm.time}
                </div>
                <div className="flex items-center gap-1 text-[10px] font-medium text-gray-500 uppercase tracking-wider">
                  <MapPin className="w-3 h-3" />
                  {alarm.location}
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>
      <div className="p-4 border-t border-gray-50">
        <button className="w-full py-2 bg-gray-50 hover:bg-gray-100 text-gray-600 text-sm font-bold rounded-xl transition-colors">
          View All Logs
        </button>
      </div>
    </div>
  );
};
