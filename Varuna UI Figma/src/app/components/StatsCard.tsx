import React from 'react';
import { LucideIcon } from 'lucide-react';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

interface StatsCardProps {
  title: string;
  value: string | number;
  icon: LucideIcon;
  trend?: {
    value: number;
    isUp: boolean;
  };
  color: 'blue' | 'green' | 'red' | 'yellow' | 'purple';
}

const colorMap = {
  blue: 'bg-blue-500/10 text-blue-500 border-blue-500/20',
  green: 'bg-green-500/10 text-green-500 border-green-500/20',
  red: 'bg-red-500/10 text-red-500 border-red-500/20',
  yellow: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20',
  purple: 'bg-purple-500/10 text-purple-500 border-purple-500/20',
};

const iconBgMap = {
  blue: 'bg-blue-500',
  green: 'bg-green-500',
  red: 'bg-red-500',
  yellow: 'bg-yellow-500',
  purple: 'bg-purple-500',
};

export const StatsCard: React.FC<StatsCardProps> = ({ title, value, icon: Icon, trend, color }) => {
  return (
    <div className={cn("p-5 rounded-2xl border bg-white shadow-sm flex flex-col justify-between h-full")}>
      <div className="flex justify-between items-start mb-4">
        <div className={cn("p-2.5 rounded-xl text-white", iconBgMap[color])}>
          <Icon className="w-5 h-5" />
        </div>
        {trend && (
          <div className={cn(
            "text-xs font-semibold px-2 py-1 rounded-full flex items-center gap-1",
            trend.isUp ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"
          )}>
            {trend.isUp ? '↑' : '↓'} {trend.value}%
          </div>
        )}
      </div>
      <div>
        <p className="text-sm font-medium text-gray-500 mb-1">{title}</p>
        <h3 className="text-2xl font-bold text-gray-900">{value}</h3>
      </div>
    </div>
  );
};
