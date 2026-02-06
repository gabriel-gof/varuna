import React from 'react';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  BarChart,
  Bar,
  Cell,
  PieChart,
  Pie
} from 'recharts';

const trafficData = [
  { time: '00:00', download: 1200, upload: 400 },
  { time: '04:00', download: 800, upload: 300 },
  { time: '08:00', download: 2400, upload: 900 },
  { time: '12:00', download: 3200, upload: 1200 },
  { time: '16:00', download: 2800, upload: 1100 },
  { time: '20:00', download: 4500, upload: 1800 },
  { time: '23:59', download: 1500, upload: 500 },
];

const signalData = [
  { range: '-30 to -25 dBm', count: 45, color: '#ef4444' },
  { range: '-25 to -20 dBm', count: 320, color: '#f59e0b' },
  { range: '-20 to -15 dBm', count: 2450, color: '#10b981' },
  { range: '-15 to -10 dBm', count: 1240, color: '#3b82f6' },
  { range: '>-10 dBm', count: 180, color: '#6366f1' },
];

export const TrafficChart = () => (
  <div className="bg-white p-6 rounded-2xl border border-gray-100 shadow-sm h-[350px]">
    <div className="flex justify-between items-center mb-6">
      <h3 className="text-lg font-bold text-gray-900">Network Traffic (Gbps)</h3>
      <select className="text-sm border-none bg-gray-50 rounded-md px-2 py-1 outline-none font-medium text-gray-600">
        <option>Last 24 Hours</option>
        <option>Last 7 Days</option>
      </select>
    </div>
    <div className="h-[250px]">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={trafficData}>
          <defs>
            <linearGradient id="colorDown" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#6366f1" stopOpacity={0.1}/>
              <stop offset="95%" stopColor="#6366f1" stopOpacity={0}/>
            </linearGradient>
            <linearGradient id="colorUp" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#10b981" stopOpacity={0.1}/>
              <stop offset="95%" stopColor="#10b981" stopOpacity={0}/>
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
          <XAxis 
            dataKey="time" 
            axisLine={false} 
            tickLine={false} 
            tick={{ fontSize: 12, fill: '#94a3b8' }} 
          />
          <YAxis 
            axisLine={false} 
            tickLine={false} 
            tick={{ fontSize: 12, fill: '#94a3b8' }} 
          />
          <Tooltip 
            contentStyle={{ borderRadius: '8px', border: 'none', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }}
          />
          <Area 
            type="monotone" 
            dataKey="download" 
            stroke="#6366f1" 
            strokeWidth={2}
            fillOpacity={1} 
            fill="url(#colorDown)" 
          />
          <Area 
            type="monotone" 
            dataKey="upload" 
            stroke="#10b981" 
            strokeWidth={2}
            fillOpacity={1} 
            fill="url(#colorUp)" 
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  </div>
);

export const SignalDistributionChart = () => (
  <div className="bg-white p-6 rounded-2xl border border-gray-100 shadow-sm h-[350px]">
    <h3 className="text-lg font-bold text-gray-900 mb-6">ONU Signal Distribution</h3>
    <div className="h-[250px]">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={signalData}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
          <XAxis 
            dataKey="range" 
            axisLine={false} 
            tickLine={false} 
            tick={{ fontSize: 10, fill: '#94a3b8' }} 
          />
          <YAxis 
            axisLine={false} 
            tickLine={false} 
            tick={{ fontSize: 12, fill: '#94a3b8' }} 
          />
          <Tooltip 
            cursor={{ fill: '#f8fafc' }}
            contentStyle={{ borderRadius: '8px', border: 'none', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }}
          />
          <Bar dataKey="count" radius={[4, 4, 0, 0]}>
            {signalData.map((entry, index) => (
              <Cell key={`cell-${index}`} fill={entry.color} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  </div>
);
