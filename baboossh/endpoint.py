import ipaddress
import asyncio, asyncssh, sys, errno
import json
from baboossh import dbConn
from baboossh import Host
from baboossh.exceptions import *

class Endpoint():
    """A SSH endpoint

    An Endpoint is a couple of an IP address and a port on which a SSH server is
    (supposed) being run.

    Attributes:
        ip (str): The IP address of the Endpoint
        port (str): The port number of the Endpoint
        id (int): The endpoint id
        host (:class:`.Host`): The Endpoint's :class:`.Host`
        scope (bool): Whether the Endpoint is in scope or not
        scanned (bool): Whether :func:`scan` has been run on the Endpoint
        reachable (bool): Whether the Endpoint was reached using :func:`scan`
        distance (int): The number of hops to reach the Endpoint, determined by :func:`scan`
        found (:class:`.Endpoint`): The Endpoint on which the current Endpoint was discovered
        auth ([str...]): A list of allowed authentication methods, populated by a :func:`scan`
    """

    search_fields = ['ip','port','auth']

    def __init__(self,ip,port):
        #check if ip is actually an IP
        ipaddress.ip_address(ip)
        if not port.isdigit():
            raise ValueError("The port is not a positive integer")

        self.ip = ip
        self.__port = port
        self.host = None
        self.id = None
        self.scope = True
        self.scanned = False
        self.reachable = None
        self.distance = None
        self.found = None
        self.auth = set()
        c = dbConn.get().cursor()
        c.execute('SELECT id,host,scanned,reachable,distance,auth,scope,found FROM endpoints WHERE ip=? AND port=?',(self.ip,self.port))
        savedEndpoint = c.fetchone()
        c.close()
        if savedEndpoint is not None:
            self.id = savedEndpoint[0]
            self.host = Host.find_one(host_id=savedEndpoint[1])
            self.scanned = savedEndpoint[2] != 0
            if savedEndpoint[3] is None:
                self.reachable = None
            else:
                self.reachable = savedEndpoint[3] != 0
            if savedEndpoint[4] is not None:
                self.distance = savedEndpoint[4]
            if savedEndpoint[5] is not None :
                self.auth = set(json.loads(savedEndpoint[5]))
            self.scope = savedEndpoint[6] != 0
            if savedEndpoint[7] is not None :
                self.found = Endpoint.find_one(endpoint_id=savedEndpoint[7])

    @property
    def port(self):
        return int(self.__port)

    @port.setter
    def port(self, port):
        self.__port = int(port)

    @property
    def connection(self):
        from baboossh import Connection
        return Connection.find_one(endpoint=self)

    def save(self):
        """Save the Endpoint in database

        If the Endpoint object has an id it means it is already stored in database,
        so it is updated. Else it is inserted and the id is set in the object.

        """

        c = dbConn.get().cursor()
        if not self.auth:
            jauth = None
        else:
            jauth = json.dumps(list(self.auth))
        if self.id is not None:
            #If we have an ID, the endpoint is already saved in the database : UPDATE
            c.execute('''UPDATE endpoints 
                SET
                    ip = ?,
                    port = ?,
                    host = ?,
                    scanned = ?,
                    reachable = ?,
                    distance = ?,
                    auth = ?,
                    scope = ?,
                    found = ?
                WHERE id = ?''',
                (self.ip, self.port, self.host.id if self.host is not None else None, self.scanned, self.reachable, self.distance, jauth, self.scope, self.found.id if self.found is not None else None, self.id))
        else:
            #The endpoint doesn't exists in database : INSERT
            c.execute('''INSERT INTO endpoints(ip,port,host,scanned,reachable,distance,auth,scope,found)
                VALUES (?,?,?,?,?,?,?,?,?) ''',
                (self.ip,self.port,self.host.id if self.host is not None else None, self.scanned, self.reachable, self.distance, jauth, self.scope, self.found.id if self.found is not None else None))
            c.close()
            c = dbConn.get().cursor()
            c.execute('SELECT id FROM endpoints WHERE ip=? AND port=?',(self.ip,self.port))
            self.id  = c.fetchone()[0]
        c.close()
        dbConn.get().commit()

    def delete(self):
        """Delete an Endpoint from the :class:`.Workspace`"""

        from baboossh import Path
        from baboossh import Connection
        if self.id is None:
            return
        if self.host is not None:
            endpoints = self.host.endpoints
            if len(endpoints) == 1:
                self.host.delete()
        for connection in Connection.find_all(endpoint=self):
            connection.delete()
        for path in Path.find_all(dst=self):
            path.delete()
        c = dbConn.get().cursor()
        c.execute('DELETE FROM endpoints WHERE id = ?',(self.id,))
        c.close()
        dbConn.get().commit()
        return

    @classmethod
    def find_all(cls, scope=None, found=None):
        """Find all Endpoints matching the criteria

        Args:
            scope (bool): 
                List Endpoints in scope (`True`), out of scope (`False`), or both (`None`)
            found (:class:`Endpoint`):
                The `Endpoint` the endpoints were discovered on
    
        Returns:
            A list of all `Endpoint`\ s in the :class:`.Workspace`
        """

        ret = []
        c = dbConn.get().cursor()
        if found is None:
            if scope is None:
                req = c.execute('SELECT ip,port FROM endpoints')
            else:
                req = c.execute('SELECT ip,port FROM endpoints WHERE scope=?',(scope,))
        else:
            if scope is None:
                req = c.execute('SELECT ip,port FROM endpoints WHERE found=?',(endpoint.id if endpoint is not None else None,))
            else:
                req = c.execute('SELECT ip,port FROM endpoints WHERE scope=? and found=?',(scope,endpoint.id if endpoint is not None else None))
        for row in req:
            ret.append(Endpoint(row[0],row[1]))
        return ret

    @classmethod
    def find_one(cls,endpoint_id=None,ip_port=None):
        """Find an `Endpoint` by its id or it's IP address:Port

        Args:
            endpoint_id (int): the `Endpoint` id to search
            ip_port (str): The IP and port as "<ip>:<port>"

        Returns:
            A single `Endpoint` or `None`.
        """

        c = dbConn.get().cursor()

        if endpoint_id is not None:
            if endpoint_id == 0:
                c.close()
                return None
            c.execute('''SELECT ip,port FROM endpoints WHERE id=?''',(endpoint_id,))
        elif ip_port is not None:
            ip,sep,port = ip_port.partition(":")
            if port == "":
                raise ValueError
            c.execute('''SELECT ip,port FROM endpoints WHERE ip=? and port=?''',(ip,port))
        else:
            c.close()
            return None

        row = c.fetchone()
        c.close()
        if row is None:
            return None
        return Endpoint(row[0],row[1])

    def __str__(self):
        return self.ip+":"+str(self.port)

    @classmethod
    def search(cls,field,val,show_all=False):
        """Search in the workspace for an `Endpoint`

        Args:
            field (str): the `Endpoint` attribute to search in
            val (str): the value to search for
            show_all (bool): whether to include out-of scope `Endpoint`\ s in search results

        Returns:
            A `List` of `Endpoint`\ s corresponding to the search.
        """

        if field not in cls.search_fields:
            raise ValueError
        ret = []
        print(field);
        c = dbConn.get().cursor()
        val = "%"+val+"%"
        if show_all:
            #Ok this sounds fugly, but there seems to be no way to set a column name in a parameter. The SQL injection risk is mitigated as field must be in allowed fields, but if you find something better I take it
            c.execute('SELECT ip,port FROM endpoints WHERE {} LIKE ?'.format(field),(val,))
        else:
            c.execute('SELECT ip,port FROM endpoints WHERE scope=? and {} LIKE ?'.format(field),(True,val))
        for row in c:
            ret.append(Endpoint(row[0],row[1]))
        return ret

    async def __asyncScan(self,gateway,gw,silent):
        #This inner class access the endpoint through the "endpoint" var as "self" keywork is changed
        endpoint = self
        class ScanSSHClient(asyncssh.SSHClient):
            def connection_made(self, conn):
                endpoint.reachable = True
        
            def auth_banner_received(self, msg, lang):
                #TODO
                pass
        
            def public_key_auth_requested(self):
                endpoint.auth.add("privkey")
                return None
        
            def password_auth_requested(self):
                endpoint.auth.add("password")
                return None
        
            def kbdint_auth_requested(self):
                endpoint.auth.add("kbdint")
                return None
        
            def auth_completed(self):
                pass

        self.scanned = True
        try:
            conn, client = await asyncio.wait_for(asyncssh.create_connection(ScanSSHClient, self.ip, port=self.port,tunnel=gw,known_hosts=None,username="user"), timeout=3.0)
        except asyncssh.Error as e:
            #Permission denied => expected behaviour
            if e.code == 14:
                pass
            else:
                print("asyncssh Error: "+str(e))
                return False
        except OSError as e:
            #No route to Host
            if e.errno == errno.EHOSTUNREACH:
                if not silent:
                    print("No route to host")
            return False
        except asyncio.TimeoutError:
            self.reachable = False
            self.save()
            if not silent:
                print("Timeout")
            return False
        try:
            conn.close()
        except:
            pass
        if not silent:
            print("Done")
        if gateway is None:
            self.distance = 0
        else:
            self.distance = gateway.distance+1
        self.save()
        return True

    def scan(self,gateway="auto",silent=False):
        """Scan the endpoint to gather information

        Scanning the endpoint allows to check if it can be reached with existing
        pathes or with a manually specified gateway. Once an Endpoint is reached,
        supported authentication are listed and stored. This function populates
        `scanned`, `reachable` and `auth`, as well as creates relevant
        :class:`.Path` object if needed.

        Args:
            gateway (`None` or `"auto"` or a :class:`.Connection`): Defines the
                gateway to use to reach the Endpoint: 

                * `None` disable the use of any gateway to try to reach directly the endpoint, 
                * `"auto"` finds an existing gateway using :func:`~endpoint.Endpoint.getGatewayConnection`
                * :class:`.Connection` uses the provided connection as a gateway

                Defaults to `"auto"`
            silent (bool): Whether the connection output is printed. Defaults to `False`
            
        Returns:
            `True` if the endpoint was reached and the scan successful, `False` otherwise.
        """
        if gateway == "auto":
            try:
                from baboossh import Connection
                gateway = Connection.find_one(gateway_to=self)
            except NoPathException as exc:
                raise exc
        if gateway is not None:
            gw = gateway.initConnect()
        else:
            gw = None
        done = asyncio.get_event_loop().run_until_complete(self.__asyncScan(gateway,gw,silent))
        try:
            gw.close()
        except:
            pass

        if gateway is None:
            gwHost = None
        else:
            gwHost = gateway.endpoint.host
            if gwHost is None:
                return done
        from baboossh import Path
        p = Path(gwHost,self)
        if done:
            p.save()
        else:
            if p.id is not None:
                p.delete()
        return done 
