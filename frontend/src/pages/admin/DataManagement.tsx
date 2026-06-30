import { useState } from 'react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  Database, FileText, Link, BookOpen, Code, Play,
} from 'lucide-react';
import Admin from '../Admin';
import Playground from '../Playground';

export default function DataManagement() {
  return (
    <div className="h-full overflow-auto">
      <h1 className="text-2xl font-bold mb-6">数据管理</h1>
      <Tabs defaultValue="datasources">
        <TabsList>
          <TabsTrigger value="datasources">
            <Database className="h-4 w-4 mr-2" />
            数据源
          </TabsTrigger>
          <TabsTrigger value="metadata">
            <FileText className="h-4 w-4 mr-2" />
            表元数据
          </TabsTrigger>
          <TabsTrigger value="relations">
            <Link className="h-4 w-4 mr-2" />
            表关联
          </TabsTrigger>
          <TabsTrigger value="templates">
            <FileText className="h-4 w-4 mr-2" />
            SQL 模板
          </TabsTrigger>
          <TabsTrigger value="terms">
            <BookOpen className="h-4 w-4 mr-2" />
            业务术语
          </TabsTrigger>
          <TabsTrigger value="playground">
            <Play className="h-4 w-4 mr-2" />
            SQL Playground
          </TabsTrigger>
        </TabsList>
        <TabsContent value="datasources">
          <Admin embeddedTab="datasources" />
        </TabsContent>
        <TabsContent value="metadata">
          <Admin embeddedTab="metadata" />
        </TabsContent>
        <TabsContent value="relations">
          <Admin embeddedTab="relations" />
        </TabsContent>
        <TabsContent value="templates">
          <Admin embeddedTab="templates" />
        </TabsContent>
        <TabsContent value="terms">
          <Admin embeddedTab="terms" />
        </TabsContent>
        <TabsContent value="playground">
          <Playground />
        </TabsContent>
      </Tabs>
    </div>
  );
}
