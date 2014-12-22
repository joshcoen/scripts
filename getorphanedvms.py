from optparse import OptionParser, make_option
from pyVim.connect import SmartConnect, Disconnect
from pyVmomi import vim, vmodl
import argparse
import atexit
import sys
import urllib2, urlparse, base64

vmxPath = []
dsVM = {}
invVM = []

#function to set the vmxPath global variable to null
def updatevmxPath():
    global vmxPath
    vmxPath = []

#function to fix any URLs that have spaces in them
#urllib for some reason doesn't like spaces
#function found on internet
def url_fix(s, charset='utf-8'):

    if isinstance(s, unicode):
        s = s.encode(charset, 'ignore')
    scheme, netloc, path, qs, anchor = urlparse.urlsplit(s)
    path = urllib2.quote(path, '/%')
    qs = urllib2.quote(qs, ':&=')
    return urlparse.urlunsplit((scheme, netloc, path, qs, anchor))


#function to parse through arguments for connecting to ESXi host or vCenter server
#function taken from getallvms.py script from pyvmomi github repo
def GetArgs():
    """
    Supports the command-line arguments listed below.
    """
    parser = argparse.ArgumentParser(description='Process args for retrieving all the Virtual Machines')
    parser.add_argument('-s', '--host', required=True, action='store', help='Remote host to connect to')
    parser.add_argument('-o', '--port', type=int, default=443,   action='store', help='Port to connect on')
    parser.add_argument('-u', '--user', required=True, action='store', help='User name to use when connecting to host')
    parser.add_argument('-p', '--password', required=True, action='store', help='Password to use when connecting to host')
    args = parser.parse_args()
    return args


#function to search for VMX files on any datastore that is passed to it
def FindVMX(dsbrowser, dsname, datacenter, fulldsname):

    args = GetArgs()
    search = vim.HostDatastoreBrowserSearchSpec()
    search.matchPattern = "*.vmx"
    searchDS = dsbrowser.SearchDatastoreSubFolders_Task(dsname, search)
    while searchDS.info.state != "success":
        pass
    results = searchDS.info.result
    #print results
    for rs in searchDS.info.result:
        dsfolder = rs.folderPath
        for f in rs.file:
            try:
                dsfile = f.path
                vmfold = dsfolder.split("]")
                vmfold = vmfold[1]
                vmfold = vmfold[1:]
                vmxurl = "https://%s/folder/%s%s?dcPath=%s&dsName=%s" % (args.host, vmfold, dsfile, datacenter, fulldsname)
                vmxPath.append(vmxurl)
            except Exception, e:
                print "Caught exception : " + str(e)
                return -1

#function to download any vmx file passed to it via the datastore browser and find the 'vc.uuid' and 'displayName'
def examineVMX(dsname):
    args = GetArgs()
    try:
        for file in vmxPath:
			#print file
			username = args.user
			password = args.password
			request = urllib2.Request(url_fix(file))
			base64string = base64.encodestring('%s:%s' % (username, password)).replace('\n', '')
			request.add_header("Authorization", "Basic %s" % base64string)
			result = urllib2.urlopen(request)
			vmxfile = result.readlines()
			mylist = []
			for a in vmxfile:
				mylist.append(a)
			for b in mylist:
				if b.startswith("displayName"):
					dn = b
				if b.startswith("vc.uuid"):
					vcid = b
			uuid = vcid.replace('"', ""); uuid = uuid.replace("vc.uuid = ", ""); uuid = uuid.strip("\n"); uuid = uuid.replace(" ", ""); uuid = uuid.replace("-", "")
			newDN = dn.replace('"', ""); newDN = newDN.replace("displayName = ", ""); newDN = newDN.strip("\n")
			vmfold = file.split("folder/"); vmfold = vmfold[1].split("/"); vmfold = vmfold[0]
			dspath = "%s/%s" % (dsname, vmfold)
			tempdsVM = [newDN, dspath]
			dsVM[uuid] = tempdsVM

    except Exception, e:
        print "Caught exception : " + str(e)


#function to get all vms in the inventory. Some parts of the function taken from the getallvms.py script from pyvmomi from github repo
#function get the instanceuuid for each VM, formats it and adds to a list
def GetVmInfo(vm, depth=1):
    """
    Print information for a particular virtual machine or recurse into a folder with depth protection
    """
    maxdepth = 10

    #if this is a group it will have children. if it does, recurse into them and then return
    if hasattr(vm, 'childEntity'):
        if depth > maxdepth:
            return
        vmList = vm.childEntity
        for c in vmList:
            GetVmInfo(c, depth+1)
        return
    if hasattr(vm, 'CloneVApp_Task'):
        vmList = vm.vm
        for c in vmList:
                GetVmInfo(c)
        return

    try:
        uuid = vm.config.instanceUuid
        uuid = uuid.replace("-", "")
        invVM.append(uuid)
    except Exception, e:
        print "Caught exception : " + str(e)
        return -1
        pass

#fucntion takes vc.uuid from the vmx file and the instanceuuid from the inventory VM and looks for match
#if no match is found it is printed out.
def Findmatch(uuid):
    a = 0
    for temp in invVM:
        if uuid == temp: a = a+1
    if a <1: print dsVM[uuid]

#function runs all of the other functions. Some parts of this function are taken from the getallvms.py script from the pyvmomi gihub repo
def main():

    args = GetArgs()
    try:
        si = None
        try:
            si = SmartConnect(host=args.host,
                              user=args.user,
                              pwd=args.password,
                              port=int(args.port))
        except IOError, e:
            pass

        if not si:
            print "Could not connect to the specified host using specified username and password"
            return -1

        atexit.register(Disconnect, si)

        content = si.RetrieveContent()
        datacenter = content.rootFolder.childEntity[0]
        datastores = datacenter.datastore
        vmFolder = datacenter.vmFolder
        vmList =  vmFolder.childEntity
        dsvmkey = []
        #each datastore found on ESXi host or vCenter is passed to the FindVMX and examineVMX functions to find all VMX files and search them
        for ds in datastores:
            FindVMX(ds.browser, "[%s]" % ds.summary.name, datacenter.name, ds.summary.name)
            examineVMX(ds.summary.name)
            updatevmxPath()
        #each VM found in the inventory is passed to the GetVmInfo function to get it's instanceuuid
        for vm in vmList:
            GetVmInfo(vm)
        #each key from thedsVM hashtable is added to a separate list for comparison later
        for a in dsVM.keys(): dsvmkey.append(a)
        #each uuid in the dsvmkey list is passed to the Findmatch function to look for a match
        print "The following virtual machine(s) do not exist in the inventory, but exist on a datastore (Display Name, Datastore/Folder name):"
        for match in dsvmkey:
            Findmatch(match)
	Disconnect(si)
    except vmodl.MethodFault, e:
        print "Caught vmodl fault : " + e.msg
        return -1
    except Exception, e:
        print "Caught exception : " + str(e)
        return -1

    return 0

# Start program
if __name__ == "__main__":
    main()